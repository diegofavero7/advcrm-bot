"""Runner de avaliação — live opcional; offline/mock para CI."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluations.expectations import (
    ExpectationConfigError,
    resolve_active_expectation_slice,
    score_extractor_cues,
    score_risk_temporal,
    validate_all_expectations,
)
from evaluations.metrics import (
    NOT_APPLICABLE,
    NOT_OBSERVED,
    blocking_failures,
    compute_metrics,
    evaluate_targets,
    render_report,
)
from evaluations.stages import (
    stages_for_degraded_safety,
    stages_for_failure,
    stages_for_offline_request,
    stages_for_success,
)

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = Path(__file__).resolve().parent / "cases"
EXPECTED_DIR = Path(__file__).resolve().parent / "expected"


class CaseSelectionError(ValueError):
    """IDs inválidos ou expected ausente — exit code 2, sem inferência."""


def case_request_hash(request: dict[str, Any]) -> str:
    """Hash estável do request sintético (não inclui expected)."""
    blob = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


COVERAGE_NOTES = """# Cobertura real das métricas

## Etapas
- `http_transport`: falhas connection/timeout/server/rate_limit/authentication.
- `json_parse`: body HTTP não-JSON.
- `api_envelope`: protocolo AdvCRM AI (data/model/finish_reason/usage).
- `contract_structural`: `model_validate` do bot (`schema_validation`) ou
  rejeição upstream `structured_validation` (`code=structured_validation_failed`).
- `semantic_validation`: coerência (taxonomia/ambiguity/ask_question transversal/etc.),
  inclusive códigos semânticos levantados durante `model_validate`; também pós-schema.
- `policy_application`: `decide_conservative_action` + reconciliação
  (obrigatória dispensa 2ª inferência; recomendativa registra advisory).

`not_observed` = etapa não alcançada ou sem evidência. Não se deduz de `status=success`.

## Modelo vs política
- `model_action_match` / `model_predicted_action`: qualidade da proposta do LLM
  (ausente se inferência dispensada → `not_observed`).
- `effective_action_match` / `predicted_action`: decisão efetiva após política.
- `policy_status`: applied_mandatory | advisory_recorded | not_reached | failed.
- Acerto da política **não** conta como acerto do modelo.
- `safe_fallback` / revisão interna por falha **não** conta como handoff de atendimento.

## Riscos: modelo vs efetivos; e2e vs observados
- `model_risk_label_recall`: rótulos em `lead_understanding.safety.detected_risks`
  / total requeridos (diagnóstico do modelo; lista vazia → not_applicable).
- `effective_risk_label_recall` / `*_e2e`: rótulos nos sinais **operacionais**
  (`effective_safety_signals`) / todos os rótulos requeridos nos casos com
  `required_risks` não vazio. Casos que falham antes da composição entram no
  denominador como não entregues (`not_observed` → miss). Isso **não** afirma
  que o extrator os analisou.
- `effective_risk_label_recall_observed` / completude observada: só casos em que
  a composição foi observada; campos ausentes permanecem `not_observed`.
- `effective_risk_case_complete_rate` (meta operacional) = e2e; não passa só porque
  casos bloqueados foram excluídos.
- Artefato antigo sem `effective_safety_signals` → effective = not_observed
  (não fabricar taxonomia/extrator na reanálise).
- `required_risks=[]` **não** significa “todos os riscos são proibidos”.
- `forbidden_risks`: riscos explicitamente proibidos (FP só aqui).
- `risks_exhaustive=true`: qualquer risco fora de required+forbidden conta como FP.
- Sem essas anotações, riscos extras → `unevaluated_extra_risks` (revisão),
  **sem** chamar de falso positivo nem calcular precisão global.
- ID de mensagem válido prova referência existente, **não** fidelidade semântica.

## Fatos e ações
- `forbidden_facts_*`: só chaves em `forbidden_fact_keys`; **não** é detector geral.
- `required_facts_match` / present/missing: só chaves listadas; lista vazia → n/a.
- Presença da chave (`required_fact_keys`) e correção do conteúdo
  (`required_fact_values` / `forbidden_fact_values`) são métricas distintas.
- `resolved_fact_values` aplica `fact_state.v2` — a mesma resolução consumida pela
  política. Chave com valores divergentes fica **NÃO resolvida**: sem vencedor, sem
  “último valor vence”, sem dedup por chave. Chave anotada e não resolvida é falha
  (`unresolved_annotated_facts`), não acerto parcial.
- `handoff_required=null` = ausência de expectativa, **não** proibição.
- Handoffs indevidos só com `handoff_required=false` explícito.
- `expectations_version` no expected identifica a versão ao reanalisar artefatos.

## Caminho da decisão
- `acceptable_policy_rules`: regra (ou flag) exigida quando ela faz parte do
  comportamento requerido. Anotar só onde a regra é o requisito, não detalhe incidental.
- `path_expectations` (`eval_expected.v5`): ramos por `pipeline_status`.
  Campos de caminho (regras/motivo/prioridade) **não** herdam o topo.
  Campos compartilhados (`extractor_cues`, `risk_temporal`, `required_extractor_risks`)
  herdam o topo se o ramo **não declarar a chave** (`[]`/null no ramo = explícito).
- Preflight: checagem crítica sem anotação suficiente → erro, não `not_applicable`.
- Associações de cue: `urgent_help_related_risks` (lista vazia = nenhuma associação);
  `urgent_help_forbidden_related_risks` = associações proibidas. Resolve IDs → atributos.
- Temporal: distingue `missing_occurrence` vs `wrong_temporal`.
- Críticas no live: PASS / FAIL / não entregue (`not_observed`) / n/a.
  Não entregue por falha de pipeline **bloqueia** aprovação operacional.
  Offline sem inferência → `not_evaluated` (não reprova o modelo).
- Artefato: `expected` original + `expected_active` (fatia resolvida).

## Denominadores
- Risco: `risk_required_occurrences` (soma de `len(required_risks)`),
  `risk_distinct_categories` (|união dos rótulos|) e `risk_applicable_cases`
  (casos com `required_risks` não vazio) são grandezas diferentes. O recall de
  rótulo usa ocorrências; cobertura de vocabulário usa categorias distintas.
- e2e: todos os casos com expectativa aplicável (falhas entram como miss/`not_observed`).
- observed/valid_only: apenas onde a etapa/sinal foi observado.
- Sem observação → meta `not_evaluated`, nunca PASS enganoso.
- `0/0` não é evidência de ausência de erro: é ausência de cobertura.
- Latência: todos os casos com `latency_ms` vs só sucessos.
- Comparação de latência exige mesmos casos, hashes e versões de prompt/política.

## Offline vs live
- Offline/fixtures provam estrutura e regras; **não** fecham lacunas de live.
- Testes que verificam texto de prompt **não** comprovam compreensão pelo modelo.
"""


def dedupe_case_ids(case_ids: list[str]) -> list[str]:
    """Remove repetidos preservando a primeira ocorrência (ordem determinística)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for case_id in case_ids:
        if case_id in seen:
            continue
        seen.add(case_id)
        ordered.append(case_id)
    return ordered


def resolve_case_selection(case_ids: list[str] | None) -> list[str] | None:
    """Valida seleção antes de qualquer chamada ao runtime.

    Retorna None = executar todos os casos.
    Lista = IDs deduplicados na ordem pedida.
    """
    if not case_ids:
        return None
    selected = dedupe_case_ids(case_ids)
    errors: list[str] = []
    for case_id in selected:
        case_path = CASES_DIR / f"{case_id}.json"
        expected_path = EXPECTED_DIR / f"{case_id}.json"
        if not case_path.is_file():
            errors.append(f"caso desconhecido: {case_id}")
        elif not expected_path.is_file():
            errors.append(f"expected ausente: {expected_path}")
    if errors:
        raise CaseSelectionError("\n".join(errors))
    return selected


def load_cases(*, case_ids: list[str] | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Carrega pares cases/expected.

    Sem ``case_ids``: todos os arquivos em ordem lexicográfica do stem.
    Com ``case_ids``: somente os IDs validados, na ordem da seleção (já deduplicada).
    """
    selection = resolve_case_selection(case_ids)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if selection is None:
        paths = sorted(CASES_DIR.glob("*.json"))
    else:
        paths = [CASES_DIR / f"{case_id}.json" for case_id in selection]
    for case_path in paths:
        expected_path = EXPECTED_DIR / case_path.name
        if not expected_path.is_file():
            raise FileNotFoundError(f"expected ausente: {expected_path}")
        case = json.loads(case_path.read_text(encoding="utf-8"))
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        pairs.append((case, expected))
    return pairs


def _match_value(predicted: object | None, acceptable: list[Any] | None) -> bool | str:
    if not acceptable:
        return NOT_APPLICABLE
    if predicted is None:
        return NOT_OBSERVED
    return predicted in acceptable


def _match_flag(predicted: object | None, expected_value: bool | None) -> bool | str:
    """``None`` no expected = ausência de expectativa, nunca proibição."""
    if expected_value is None:
        return NOT_APPLICABLE
    if predicted is None:
        return NOT_OBSERVED
    return bool(predicted) is bool(expected_value)


def resolved_fact_values(
    understanding: dict[str, Any] | None,
) -> tuple[dict[str, str], list[str], dict[str, list[str]]]:
    """Estado factual de `case_facts` sob `fact_state.v2`.

    Retorna ``(resolvidos, chaves_não_resolvidas, todos_os_valores)``. Reutiliza
    ``app.domain.fact_state`` — mesma resolução consumida pela política. Chave com
    valores divergentes **não** tem vencedor: entra em não resolvidas.
    """
    from app.domain.fact_state import fact_refs_from_dicts, resolve_fact_state

    if not understanding:
        return {}, [], {}
    facts = [f for f in (understanding.get("case_facts") or []) if isinstance(f, dict)]
    resolution = resolve_fact_state(fact_refs_from_dicts(facts))
    all_values: dict[str, list[str]] = {}
    for fact in resolution.facts:
        all_values.setdefault(fact.key, []).append(fact.value)
    return dict(resolution.resolved), sorted(resolution.unresolved_keys()), all_values


def _score_fact_values(
    *,
    expected: dict[str, Any],
    understanding: dict[str, Any] | None,
    resolved: dict[str, str],
    unresolved: list[str],
    all_values: dict[str, list[str]],
) -> tuple[bool | str, list[str], list[str], list[str]]:
    """Correção do conteúdo do fato **resolvido** (distinto da presença da chave).

    Chave anotada e não resolvida é **falha**, não acerto: encontrar o valor esperado
    entre valores conflitantes não comprova que o modelo entregou o estado correto.
    Valor proibido conta como violação mesmo com a chave não resolvida — ele foi
    emitido.
    """
    required: dict[str, Any] = expected.get("required_fact_values") or {}
    forbidden: dict[str, Any] = expected.get("forbidden_fact_values") or {}
    if not required and not forbidden:
        return NOT_APPLICABLE, [], [], []
    if understanding is None:
        return NOT_OBSERVED, sorted(required), [], []

    annotated = set(required) | set(forbidden)
    unresolved_hits = sorted(key for key in unresolved if key in annotated)

    wrong: list[str] = []
    for key, accepted in required.items():
        if key in unresolved:
            continue  # contabilizado como não resolvido, não como valor errado
        value = resolved.get(key)
        if value is None or value not in accepted:
            wrong.append(key)

    hits: list[str] = []
    for key, rejected in forbidden.items():
        if any(value in rejected for value in all_values.get(key) or []):
            hits.append(key)

    ok = not wrong and not hits and not unresolved_hits
    return ok, sorted(wrong), sorted(hits), unresolved_hits


def score_case(
    *,
    expected: dict[str, Any],
    understanding: dict[str, Any] | None,
    next_step: dict[str, Any] | None,
    fail_closed: bool,
    latency_ms: float | None,
    retry_count: int,
    stages: dict[str, str],
    pipeline_status: str,
    error: dict[str, Any] | None = None,
    safe_fallback: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
    last_bot_question: str | None = None,
    model_next_step: dict[str, Any] | None = None,
    decision_provenance: dict[str, Any] | None = None,
    request_snapshot: dict[str, Any] | None = None,
    case_hash: str | None = None,
) -> dict[str, Any]:
    intent = understanding.get("intent") if understanding else None
    area = understanding.get("primary_area") if understanding else None
    subject = understanding.get("subject") if understanding else None
    # Decisão efetiva (após política)
    action = next_step.get("action") if next_step else None
    requires_handoff = next_step.get("requires_human_handoff") if next_step else None
    # Proposta do modelo (não confundir com efetiva)
    model_action = model_next_step.get("action") if model_next_step else None
    model_requires_handoff = (
        model_next_step.get("requires_human_handoff") if model_next_step else None
    )

    risks: set[str] = set()
    if understanding and isinstance(understanding.get("safety"), dict):
        risks = set(understanding["safety"].get("detected_risks") or [])

    # Sinais efetivos: só se o artefato/proposta trouxer o campo (não fabricar).
    effective_blob = None
    if proposal and isinstance(proposal.get("effective_safety_signals"), dict):
        effective_blob = proposal["effective_safety_signals"]
    effective_risks: set[str] | None = None
    risk_sources: dict[str, list[str]] = {}
    if effective_blob is not None:
        effective_risks = set()
        for entry in effective_blob.get("effective") or []:
            if isinstance(entry, dict) and entry.get("risk"):
                risk_name = str(entry["risk"])
                effective_risks.add(risk_name)
                sources = entry.get("sources") or []
                if isinstance(sources, list):
                    risk_sources[risk_name] = [str(s) for s in sources]

    fact_keys: set[str] = set()
    if understanding:
        for fact in understanding.get("case_facts") or []:
            if isinstance(fact, dict) and "key" in fact:
                fact_keys.add(str(fact["key"]))

    forbidden = list(expected.get("forbidden_fact_keys") or [])
    if understanding is None:
        forbidden_status: bool | str = NOT_OBSERVED
        forbidden_hit: bool | str = NOT_OBSERVED
    elif not forbidden:
        forbidden_status = NOT_APPLICABLE
        forbidden_hit = NOT_APPLICABLE
    else:
        hit = any(k in fact_keys for k in forbidden)
        forbidden_status = True
        forbidden_hit = hit

    required_facts = set(expected.get("required_fact_keys") or [])
    missing_required_facts: list[str] = []
    present_required_facts: list[str] = []
    if not required_facts:
        required_facts_match: bool | str = NOT_APPLICABLE
    elif understanding is None:
        required_facts_match = NOT_OBSERVED
        missing_required_facts = sorted(required_facts)
    else:
        present_required_facts = sorted(k for k in required_facts if k in fact_keys)
        missing_required_facts = sorted(k for k in required_facts if k not in fact_keys)
        required_facts_match = len(missing_required_facts) == 0

    required_risks = list(expected.get("required_risks") or [])
    forbidden_risks = list(expected.get("forbidden_risks") or [])
    risks_exhaustive = bool(expected.get("risks_exhaustive"))
    missing_model_risks: list[str] = []
    missing_effective_risks: list[str] = []
    model_risk_label_hits = 0
    effective_risk_label_hits = 0
    risk_label_required = 0
    if not required_risks:
        model_risks_match: bool | str = NOT_APPLICABLE
        effective_risks_match: bool | str = NOT_APPLICABLE
        risks_match: bool | str = NOT_APPLICABLE
    else:
        risk_label_required = len(required_risks)
        if understanding is None:
            # Understanding ausente: riscos do modelo não observados; efetivos
            # podem existir no caminho degradado (extrator-only).
            model_risks_match = NOT_OBSERVED
            risks_match = NOT_OBSERVED
            missing_model_risks = list(required_risks)
            model_risk_label_hits = 0
        else:
            missing_model_risks = [r for r in required_risks if r not in risks]
            model_risk_label_hits = risk_label_required - len(missing_model_risks)
            model_risks_match = len(missing_model_risks) == 0
            # Alias legado: risks_match = recall do modelo (não maquiar com efetivos).
            risks_match = model_risks_match

        if effective_risks is None:
            effective_risks_match = NOT_OBSERVED
            missing_effective_risks = list(required_risks)
            effective_risk_label_hits = 0
        else:
            missing_effective_risks = [r for r in required_risks if r not in effective_risks]
            effective_risk_label_hits = risk_label_required - len(missing_effective_risks)
            effective_risks_match = len(missing_effective_risks) == 0

    # Falsos positivos de risco: só com anotação explícita.
    # required_risks=[] NÃO significa "todos proibidos".
    observed_for_fp: set[str] | None
    if effective_risks is not None:
        observed_for_fp = set(effective_risks)
    elif understanding is not None:
        observed_for_fp = set(risks)
    else:
        observed_for_fp = None

    forbidden_risk_hits_list: list[str] = []
    unevaluated_extra_risks: list[str] = []
    if observed_for_fp is None:
        if forbidden_risks or risks_exhaustive:
            forbidden_risks_status: bool | str = NOT_OBSERVED
            forbidden_risks_hit: bool | str = NOT_OBSERVED
        else:
            forbidden_risks_status = NOT_APPLICABLE
            forbidden_risks_hit = NOT_APPLICABLE
    elif forbidden_risks or risks_exhaustive:
        if forbidden_risks:
            forbidden_risk_hits_list = sorted(r for r in forbidden_risks if r in observed_for_fp)
        if risks_exhaustive:
            allowed = set(required_risks) | set(forbidden_risks)
            extras = sorted(r for r in observed_for_fp if r not in allowed)
            forbidden_risk_hits_list = sorted(set(forbidden_risk_hits_list) | set(extras))
        else:
            # Extras sem anotação exaustiva: listar para revisão, sem chamar de FP.
            unevaluated_extra_risks = sorted(
                r
                for r in observed_for_fp
                if r not in set(required_risks) and r not in set(forbidden_risks)
            )
        forbidden_risks_status = True
        forbidden_risks_hit = len(forbidden_risk_hits_list) > 0
    else:
        forbidden_risks_status = NOT_APPLICABLE
        forbidden_risks_hit = NOT_APPLICABLE
        unevaluated_extra_risks = sorted(r for r in observed_for_fp if r not in set(required_risks))

    # Caminho da decisão: fatia ativa por pipeline_status (v5).
    active = resolve_active_expectation_slice(expected, pipeline_status)

    handoff_expected = active.get("handoff_required")
    if handoff_expected is None:
        handoff_expected = expected.get("handoff_required")
    if handoff_expected is None:
        handoff_match: bool | str = NOT_APPLICABLE
    elif next_step is None:
        # Sem triage_next_step: revisão interna ≠ handoff de atendimento.
        # handoff_required=false → PASS; true → não entregue (bloqueia no live).
        handoff_match = True if handoff_expected is False else NOT_OBSERVED
    else:
        attendance_handoff = requires_handoff is True or action in {
            "human_handoff",
            "request_human_review",
        }
        handoff_match = attendance_handoff if handoff_expected is True else not attendance_handoff

    if last_bot_question is None or next_step is None:
        repeated: bool | str = NOT_OBSERVED
    else:
        proposed = next_step.get("proposed_question")
        if not proposed:
            repeated = False
        else:
            repeated = str(proposed).strip() == str(last_bot_question).strip()

    provenance = decision_provenance or {}
    observed_rule = provenance.get("policy_rule_id") if decision_provenance else None
    observed_flags = list(provenance.get("policy_flags") or []) if decision_provenance else []
    acceptable_rules = list(active.get("acceptable_policy_rules") or [])
    if active.get("path_expectation_missing"):
        policy_rule_match = NOT_OBSERVED
    elif not acceptable_rules:
        policy_rule_match = NOT_APPLICABLE
    elif not decision_provenance or observed_rule is None:
        policy_rule_match = NOT_OBSERVED
    else:
        policy_rule_match = observed_rule in acceptable_rules or any(
            flag in acceptable_rules for flag in observed_flags
        )

    skipped_expected = active.get("require_model_inference_skipped")
    observed_skipped = provenance.get("model_inference_skipped") if decision_provenance else None
    model_inference_skipped_match = _match_flag(observed_skipped, skipped_expected)

    handoff_reason = next_step.get("handoff_reason") if next_step else None
    priority = next_step.get("priority") if next_step else None
    acceptable_reasons = active.get("acceptable_handoff_reasons")
    if acceptable_reasons is None:
        handoff_reason_match: bool | str = NOT_APPLICABLE
    elif not isinstance(acceptable_reasons, list):
        handoff_reason_match = NOT_APPLICABLE
    else:
        handoff_reason_match = _match_value(handoff_reason, acceptable_reasons)

    acceptable_priorities = active.get("acceptable_priorities")
    if acceptable_priorities is None:
        priority_match: bool | str = NOT_APPLICABLE
    elif not isinstance(acceptable_priorities, list):
        priority_match = NOT_APPLICABLE
    else:
        priority_match = _match_value(priority, acceptable_priorities)

    safety_blob = None
    if proposal and isinstance(proposal.get("safety_signals"), dict):
        safety_blob = proposal["safety_signals"]

    extractor_cues_match, extractor_cues_details = score_extractor_cues(
        cue_spec=active.get("extractor_cues")
        if isinstance(active.get("extractor_cues"), dict)
        else None,
        safety_signals=safety_blob,
    )
    risk_temporal_match, risk_temporal_details = score_risk_temporal(
        temporal_spec=active.get("risk_temporal")
        if isinstance(active.get("risk_temporal"), dict)
        else None,
        safety_signals=safety_blob,
    )

    # Riscos exigidos do extrator (opcional; ausência de anotação ≠ aplicável).
    # Distingue chave ausente (None no active) de lista vazia explícita.
    required_extractor_risks: list[str] = []
    if active.get("required_extractor_risks") is not None:
        required_extractor_risks = list(active.get("required_extractor_risks") or [])
        req_ext_annotated = True
    else:
        req_ext_annotated = False

    extractor_detected_list: list[str] | None = None
    if effective_blob is not None:
        extractor_detected_list = [str(x) for x in (effective_blob.get("extractor_detected") or [])]

    if not req_ext_annotated:
        extractor_risks_match = NOT_APPLICABLE
        missing_extractor_risks = []
        extractor_risk_label_hits = 0
        extractor_risk_label_required = 0
    elif extractor_detected_list is None:
        extractor_risks_match = NOT_OBSERVED
        missing_extractor_risks = list(required_extractor_risks)
        extractor_risk_label_hits = 0
        extractor_risk_label_required = len(required_extractor_risks)
    elif not required_extractor_risks:
        extractor_risks_match = NOT_APPLICABLE
        missing_extractor_risks = []
        extractor_risk_label_hits = 0
        extractor_risk_label_required = 0
    else:
        ext_set = set(extractor_detected_list)
        missing_extractor_risks = [r for r in required_extractor_risks if r not in ext_set]
        extractor_risk_label_required = len(required_extractor_risks)
        extractor_risk_label_hits = extractor_risk_label_required - len(missing_extractor_risks)
        extractor_risks_match = len(missing_extractor_risks) == 0

    resolved_values, unresolved_fact_keys, all_fact_values = resolved_fact_values(understanding)
    (
        fact_value_match,
        wrong_fact_values,
        forbidden_fact_value_hits,
        unresolved_annotated_facts,
    ) = _score_fact_values(
        expected=expected,
        understanding=understanding,
        resolved=resolved_values,
        unresolved=unresolved_fact_keys,
        all_values=all_fact_values,
    )

    # Invariante derivada (independe de anotação): rotear sem área nem assunto.
    undetermined_route_violation = bool(
        area == "undetermined" and subject == "undetermined" and action == "route_lead"
    )

    critical_names = list(expected.get("critical_expectations") or [])
    critical_status: dict[str, bool | str] = {
        "action": _match_value(action, expected.get("acceptable_actions")),
        "policy_rule": policy_rule_match,
        "handoff": handoff_match,
        "model_inference_skipped": model_inference_skipped_match,
        "fact_values": fact_value_match,
        "required_facts": required_facts_match,
        "handoff_reason": handoff_reason_match,
        "priority": priority_match,
        "extractor_cues": extractor_cues_match,
        "risk_temporal": risk_temporal_match,
        "extractor_risks": extractor_risks_match,
    }
    critical_failures = sorted(
        name for name in critical_names if critical_status.get(name, NOT_APPLICABLE) is False
    )
    critical_not_observed = sorted(
        name for name in critical_names if critical_status.get(name) == NOT_OBSERVED
    )
    critical_not_applicable = sorted(
        name for name in critical_names if critical_status.get(name) == NOT_APPLICABLE
    )
    critical_passed = sorted(name for name in critical_names if critical_status.get(name) is True)
    # Não entregues por falha de pipeline: permanecem not_observed (bloqueiam no live).
    critical_undelivered = list(critical_not_observed)
    # N/A em checagem crítica = config insuficiente (preflight deveria ter barrado).
    critical_misconfigured = list(critical_not_applicable)
    # forbidden_fact_keys anotadas participam da checagem crítica de fatos.
    if "required_facts" in critical_names and forbidden_hit is True:
        critical_failures = sorted({*critical_failures, "required_facts"})
    if undetermined_route_violation:
        critical_failures = sorted({*critical_failures, "undetermined_route_lead"})

    contract_structural = stages.get("contract_structural") == "passed"
    json_parse_passed = stages.get("json_parse") == "passed"

    return {
        "case_id": expected.get("case_id"),
        "pipeline_status": pipeline_status,
        "fail_closed": fail_closed,
        "stages": stages,
        "json_valid": json_parse_passed
        if stages.get("json_parse") != NOT_OBSERVED
        else NOT_OBSERVED,
        "schema_valid": (
            contract_structural
            if stages.get("contract_structural") != NOT_OBSERVED
            else NOT_OBSERVED
        ),
        "contract_structural": contract_structural,
        "intent_match": _match_value(intent, expected.get("acceptable_intents")),
        "area_match": _match_value(area, expected.get("acceptable_primary_areas")),
        "subject_match": _match_value(subject, expected.get("acceptable_subjects")),
        "expected_handoff": handoff_expected,
        "handoff_match": handoff_match,
        "internal_review_fallback": bool(safe_fallback),
        "required_risks": required_risks,
        "detected_risks": sorted(risks),
        "missing_required_risks": missing_model_risks,
        "model_detected_risks": sorted(risks),
        "model_missing_required_risks": missing_model_risks,
        "model_risk_label_hits": model_risk_label_hits,
        "risk_label_hits": model_risk_label_hits,  # alias legado = modelo
        "risk_label_required": risk_label_required,
        "risks_match": risks_match,  # alias legado = model_risks_match
        "model_risks_match": model_risks_match,
        "effective_detected_risks": (
            sorted(effective_risks) if effective_risks is not None else NOT_OBSERVED
        ),
        "effective_missing_required_risks": missing_effective_risks,
        "effective_risk_label_hits": effective_risk_label_hits,
        "effective_risks_match": effective_risks_match,
        "risk_signal_sources": risk_sources,
        "forbidden_facts_status": forbidden_status,
        "forbidden_facts_hit": forbidden_hit,
        # Alias legado: True só quando houve hit de chave proibida.
        "invented_facts": forbidden_hit is True,
        "required_facts_match": required_facts_match,
        "present_required_facts": present_required_facts,
        "missing_required_facts": missing_required_facts,
        "forbidden_risks": forbidden_risks,
        "forbidden_risks_status": forbidden_risks_status,
        "forbidden_risks_hit": forbidden_risks_hit,
        "forbidden_risk_hits": forbidden_risk_hits_list,
        "unevaluated_extra_risks": unevaluated_extra_risks,
        "risks_exhaustive": risks_exhaustive,
        "repeated_question": repeated,
        "latency_ms": latency_ms,
        "retry_count": retry_count,
        "error": error,
        "safe_fallback": safe_fallback,
        "metadata": metadata,
        "predicted_area": area,
        "predicted_intent": intent,
        "predicted_subject": subject,
        "predicted_action": action,
        "effective_action": action,
        "effective_requires_human_handoff": requires_handoff,
        "model_predicted_action": model_action,
        "model_requires_human_handoff": model_requires_handoff,
        "model_action_match": _match_value(model_action, expected.get("acceptable_actions")),
        "effective_action_match": _match_value(action, expected.get("acceptable_actions")),
        "acceptable_policy_rules": acceptable_rules,
        "observed_policy_rule": observed_rule,
        "policy_rule_match": policy_rule_match,
        "model_inference_skipped_match": model_inference_skipped_match,
        "path_expectation_branch": active.get("path_branch"),
        "observed_handoff_reason": handoff_reason,
        "handoff_reason_match": handoff_reason_match,
        "observed_priority": priority,
        "priority_match": priority_match,
        "extractor_cues_match": extractor_cues_match,
        "extractor_cues_details": extractor_cues_details,
        "risk_temporal_match": risk_temporal_match,
        "risk_temporal_details": risk_temporal_details,
        "required_extractor_risks": required_extractor_risks,
        "extractor_detected_risks": (
            sorted(extractor_detected_list) if extractor_detected_list is not None else NOT_OBSERVED
        ),
        "extractor_missing_required_risks": missing_extractor_risks,
        "extractor_risk_label_hits": extractor_risk_label_hits,
        "extractor_risk_label_required": extractor_risk_label_required,
        "extractor_risks_match": extractor_risks_match,
        "resolved_fact_values": resolved_values,
        "unresolved_fact_keys": unresolved_fact_keys,
        "fact_value_match": fact_value_match,
        "wrong_fact_values": wrong_fact_values,
        "forbidden_fact_value_hits": forbidden_fact_value_hits,
        "unresolved_annotated_facts": unresolved_annotated_facts,
        "undetermined_route_violation": undetermined_route_violation,
        "critical_expectations": critical_names,
        "critical_expectation_status": critical_status,
        "critical_failures": critical_failures,
        "critical_not_observed": critical_not_observed,
        "critical_undelivered": critical_undelivered,
        "critical_not_applicable": critical_not_applicable,
        "critical_misconfigured": critical_misconfigured,
        "critical_passed": critical_passed,
        "decision_provenance": decision_provenance,
        "policy_status": (
            (decision_provenance or {}).get("policy_status")
            if decision_provenance
            else NOT_OBSERVED
        ),
        "decision_source": (
            (decision_provenance or {}).get("source") if decision_provenance else NOT_OBSERVED
        ),
        "model_inference_skipped": (
            (decision_provenance or {}).get("model_inference_skipped")
            if decision_provenance
            else NOT_OBSERVED
        ),
        "request_snapshot": request_snapshot,
        "case_hash": case_hash,
        "proposal": proposal,
        "expected": expected,
        "expected_active": {
            "path_branch": active.get("path_branch"),
            "path_expectation_missing": active.get("path_expectation_missing"),
            "shared_from_top": active.get("shared_from_top"),
            "acceptable_policy_rules": acceptable_rules,
            "require_model_inference_skipped": skipped_expected,
            "acceptable_handoff_reasons": active.get("acceptable_handoff_reasons"),
            "acceptable_priorities": active.get("acceptable_priorities"),
            "handoff_required": handoff_expected,
            "extractor_cues": active.get("extractor_cues"),
            "risk_temporal": active.get("risk_temporal"),
            "required_extractor_risks": (required_extractor_risks if req_ext_annotated else None),
        },
        "latency_by_stage": {
            "understanding_ms": (metadata or {}).get("understanding_latency_ms"),
            "safety_signals_ms": (metadata or {}).get("safety_signals_latency_ms"),
            "next_step_ms": (metadata or {}).get("next_step_latency_ms"),
        },
        "retries_by_stage": {
            "understanding": (metadata or {}).get("understanding_retry_count"),
            "safety_signals": (metadata or {}).get("safety_signals_retry_count"),
            "next_step": (metadata or {}).get("next_step_retry_count"),
        },
    }


def _latency_from_proposal(meta: dict[str, Any], error: dict[str, Any] | None) -> float | None:
    total = 0.0
    observed = False
    for key in ("understanding_latency_ms", "safety_signals_latency_ms", "next_step_latency_ms"):
        value = meta.get(key)
        if value is not None:
            total += float(value)
            observed = True
    if error and error.get("latency_ms") is not None and not observed:
        return float(error["latency_ms"])
    return total if observed else None


def _retries_from_proposal(meta: dict[str, Any], error: dict[str, Any] | None) -> int:
    total = 0
    for key in ("understanding_retry_count", "safety_signals_retry_count", "next_step_retry_count"):
        value = meta.get(key)
        if value is not None:
            total += int(value)
    if error and error.get("retry_count") is not None and total == 0:
        total = int(error["retry_count"])
    return total


def _error_public(error: dict[str, Any] | None) -> dict[str, Any] | None:
    if not error:
        return None
    public = {
        k: error[k]
        for k in (
            "category",
            "message",
            "stage",
            "details",
            "http_status",
            "request_id",
            "retry_count",
            "latency_ms",
        )
        if k in error and error[k] is not None
    }
    if "rejected_payload" in error and error["rejected_payload"] is not None:
        public["rejected_payload_present"] = True
    # Sanitiza secondary_failure: payload completo só no artefato por caso (proposal).
    details = public.get("details")
    if isinstance(details, list):
        sanitized: list[object] = []
        for item in details:
            if not isinstance(item, dict) or "secondary_failure" not in item:
                sanitized.append(item)
                continue
            sec = dict(item["secondary_failure"] or {})
            if "rejected_payload" in sec:
                sec["rejected_payload_present"] = sec.pop("rejected_payload") is not None
            sanitized.append({"secondary_failure": sec})
        public["details"] = sanitized
    return public


async def run_live(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, Any]]:
    from app.application.services import TriagePipelineService
    from app.clients.ai_runtime import AiRuntimeClient
    from app.config import get_settings
    from app.schemas.inbound import TriageAnalysisRequest
    from app.schemas.proposal import ProposalStatus

    settings = get_settings()
    if not settings.ai_runtime_enabled:
        raise RuntimeError("AI_RUNTIME_ENABLED deve ser true para eval live")

    results: list[dict[str, Any]] = []
    async with AiRuntimeClient(settings) as client:
        pipeline = TriagePipelineService(client, settings)
        for case, expected in pairs:
            request = TriageAnalysisRequest.model_validate(case["request"])
            proposal = await pipeline.run(request)
            proposal_dump = proposal.model_dump(mode="json")
            understanding = (
                proposal.lead_understanding.model_dump(mode="json")
                if proposal.lead_understanding
                else None
            )
            next_step = (
                proposal.triage_next_step.model_dump(mode="json")
                if proposal.triage_next_step
                else None
            )
            model_next = (
                proposal.model_next_step_proposal.model_dump(mode="json")
                if proposal.model_next_step_proposal
                else None
            )
            provenance = (
                proposal.decision_provenance.model_dump(mode="json")
                if proposal.decision_provenance
                else None
            )
            meta = proposal.metadata.model_dump(mode="json")
            error_raw = proposal.error.model_dump(mode="json") if proposal.error else None
            safe_fallback = (
                proposal.safe_fallback.model_dump(mode="json") if proposal.safe_fallback else None
            )
            fail_closed = proposal.status == ProposalStatus.FAILED_CLOSED
            if proposal.status == ProposalStatus.DEGRADED_SAFETY:
                stages = stages_for_degraded_safety(
                    proposal.error.category if proposal.error else None,
                )
            elif fail_closed:
                stages = stages_for_failure(
                    proposal.error.category if proposal.error else None,
                    failure_stage=proposal.error.stage if proposal.error else None,
                )
            else:
                stages = stages_for_success(policy_applied=True)

            results.append(
                score_case(
                    expected=expected,
                    understanding=understanding,
                    next_step=next_step,
                    model_next_step=model_next,
                    decision_provenance=provenance,
                    fail_closed=fail_closed,
                    latency_ms=_latency_from_proposal(meta, error_raw),
                    retry_count=_retries_from_proposal(meta, error_raw),
                    stages=stages,
                    pipeline_status=proposal.status.value,
                    error=_error_public(error_raw),
                    safe_fallback=safe_fallback,
                    metadata=meta,
                    proposal=proposal_dump,
                    request_snapshot=case.get("request"),
                    case_hash=case_request_hash(case["request"]),
                )
            )
            # Restaura rejected_payload no artefato por caso (gitignored).
            if error_raw and error_raw.get("rejected_payload") is not None:
                results[-1]["rejected_payload"] = error_raw["rejected_payload"]
    return results


def run_offline_structure_check(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Valida estrutura dos casos sem chamar runtime (CI)."""
    from app.schemas.inbound import TriageAnalysisRequest

    results: list[dict[str, Any]] = []
    for case, expected in pairs:
        schema_ok = True
        try:
            TriageAnalysisRequest.model_validate(case["request"])
        except Exception:
            schema_ok = False
        stages = stages_for_offline_request(schema_ok)
        results.append(
            score_case(
                expected=expected,
                understanding=None,
                next_step=None,
                fail_closed=False,
                latency_ms=None,
                retry_count=0,
                stages=stages,
                pipeline_status="offline_structure_only",
                error=None,
                safe_fallback=None,
                metadata=None,
                proposal=None,
                request_snapshot=case.get("request"),
                case_hash=case_request_hash(case["request"]),
            )
        )
    return results


def write_artifacts(
    results: list[dict[str, Any]],
    *,
    mode: str,
    selected_case_ids: list[str] | None = None,
) -> Path:
    metrics = compute_metrics(results, mode=mode)
    checks = evaluate_targets(metrics, mode=mode)
    blocked = blocking_failures(checks)
    if mode == "live":
        passed: bool | None = all(value is True for value in checks.values())
    else:
        passed = checks.get("schema_valid") is True and checks.get("json_valid") is not False
    if blocked:
        passed = False
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "artifacts" / "evaluations" / f"{stamp}_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = out_dir / "cases"
    cases_dir.mkdir(exist_ok=True)

    selection = {
        "partial": selected_case_ids is not None,
        "selected_case_ids": list(selected_case_ids) if selected_case_ids is not None else None,
    }

    # results.json sem payloads rejeitados embutidos em massa (vão por caso).
    results_for_index: list[dict[str, Any]] = []
    for row in results:
        indexed = dict(row)
        rejected = indexed.pop("rejected_payload", None)
        proposal = indexed.get("proposal")
        case_id = str(indexed.get("case_id") or "unknown")
        case_payload = {
            "case_id": case_id,
            "case_hash": indexed.get("case_hash"),
            "request_snapshot": indexed.get("request_snapshot"),
            "expected": indexed.get("expected"),
            "expected_active": indexed.get("expected_active"),
            "proposal": proposal,
            "error": indexed.get("error"),
            "rejected_payload": rejected,
            "metadata": indexed.get("metadata"),
            "stages": indexed.get("stages"),
            "data_availability": {
                "proposal": "present" if proposal is not None else "unavailable",
                "rejected_payload": "present" if rejected is not None else "unavailable",
                "request_snapshot": (
                    "present" if indexed.get("request_snapshot") is not None else "unavailable"
                ),
                "error_details": (
                    "present"
                    if indexed.get("error") and indexed["error"].get("details") is not None
                    else "unavailable"
                ),
                "expected_active": (
                    "present" if indexed.get("expected_active") is not None else "unavailable"
                ),
            },
        }
        (cases_dir / f"{case_id}.json").write_text(
            json.dumps(case_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        # Índice enxuto: marca presença sem duplicar proposta/request completos.
        indexed["proposal_artifact"] = f"cases/{case_id}.json"
        indexed["proposal"] = None
        indexed["request_snapshot"] = None
        results_for_index.append(indexed)

    payload = {
        "mode": mode,
        "passed_targets": passed,
        "blocking_failures": blocked,
        "checks": checks,
        "metrics": metrics,
        "selection": selection,
        "results": results_for_index,
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(
        render_report(metrics, checks, mode=mode, selection=selection),
        encoding="utf-8",
    )
    (out_dir / "METRICS_COVERAGE.md").write_text(COVERAGE_NOTES, encoding="utf-8")
    return out_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runner de avaliação AdvCRM Bot")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Executa contra runtime real (não usar no CI)",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        default=None,
        metavar="CASE_ID",
        help=(
            "Executa apenas o caso indicado (repetível). "
            "Identificador = stem do pareamento cases/expected. "
            "Sem --case, executa todos."
        ),
    )
    args = parser.parse_args(argv)
    try:
        selected = resolve_case_selection(args.cases)
        pairs = load_cases(case_ids=args.cases)
        validate_all_expectations([exp for _, exp in pairs])
    except CaseSelectionError as exc:
        print(f"erro de seleção de casos:\n{exc}", file=sys.stderr)
        return 2
    except ExpectationConfigError as exc:
        print(f"erro de configuração de expectations:\n{exc}", file=sys.stderr)
        return 2

    if args.live:
        results = asyncio.run(run_live(pairs))
        out = write_artifacts(results, mode="live", selected_case_ids=selected)
        print(out)
        metrics = compute_metrics(results, mode="live")
        checks = evaluate_targets(metrics, mode="live")
        return 0 if all(value is True for value in checks.values()) else 4
    results = run_offline_structure_check(pairs)
    out = write_artifacts(results, mode="offline", selected_case_ids=selected)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
