#!/usr/bin/env python3
"""Gera o conjunto RESERVADO de comparação de understanding.

Anotações feitas ANTES de qualquer inferência. Não acrescentar estes exemplos aos
prompts. Após uso em ajustes, o conjunto deixa de ser benchmark independente.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "evaluations" / "compare" / "cases"
EXPECTED_DIR = ROOT / "evaluations" / "compare" / "expected"
SYNTHETIC_DIR = ROOT / "evaluations" / "compare" / "synthetic"

EXPECTATIONS_VERSION = "eval_expected.v3"
RESERVED_SUITE = "understanding_compare_reserved.v1"


def dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def msg(mid: str, text: str, **kwargs: object) -> dict:
    return {
        "message_id": mid,
        "role": kwargs.get("role", "lead"),
        "direction": kwargs.get("direction", "inbound"),
        "content_type": "text",
        "text": text,
        "created_at": kwargs.get("created_at", "2026-09-20T15:00:00-03:00"),
        "reply_to_message_id": kwargs.get("reply_to"),
    }


def request(case_id: str, messages: list[dict], **kwargs: object) -> dict:
    payload = {
        "event_id": f"cmp-{case_id}",
        "tenant_id": "tenant-compare",
        "lead_id": f"lead-{case_id}",
        "conversation_id": f"conv-{case_id}",
        "triage_state": "pending_classification",
        "source": "whatsapp",
        "messages": messages,
        "known_facts": [],
        "previous_decision": None,
    }
    payload.update(kwargs)
    return payload


def expected(
    case_id: str,
    *,
    intents: list[str],
    primary_areas: list[str],
    subjects: list[str] | None = None,
    required_facts: list[str] | None = None,
    forbidden_facts: list[str] | None = None,
    required_fact_values: dict[str, list[str]] | None = None,
    forbidden_fact_values: dict[str, list[str]] | None = None,
    risks: list[str] | None = None,
    forbidden_risks: list[str] | None = None,
    annotation_rationale: str,
) -> dict:
    return {
        "case_id": case_id,
        "expectations_version": EXPECTATIONS_VERSION,
        "reserved_suite": RESERVED_SUITE,
        "acceptable_intents": intents,
        "acceptable_primary_areas": primary_areas,
        "acceptable_secondary_areas": [],
        "acceptable_subjects": subjects or [],
        "acceptable_actions": [],
        "acceptable_policy_rules": [],
        "require_model_inference_skipped": None,
        "handoff_required": None,
        "required_risks": risks or [],
        "forbidden_risks": forbidden_risks or [],
        "risks_exhaustive": False,
        "required_fact_keys": required_facts or [],
        "forbidden_fact_keys": forbidden_facts or ["win_probability", "eligibility_confirmed"],
        "required_fact_values": required_fact_values or {},
        "forbidden_fact_values": forbidden_fact_values or {},
        "critical_expectations": [],
        "annotation_rationale": annotation_rationale,
        "must_not_repeat_question": None,
    }


def base_understanding(**overrides: object) -> dict:
    payload: dict = {
        "schema_version": "lead_understanding.v1",
        "intent": "new_legal_lead",
        "language": "pt-BR",
        "primary_area": "undetermined",
        "secondary_area": None,
        "subject": "undetermined",
        "subsubjects": [],
        "confidence": 0.85,
        "reasoning_summary": "síntese curta do relato",
        "participants": [],
        "case_facts": [],
        "procedural_situation": {
            "stage": None,
            "prior_request": None,
            "prior_denial": None,
            "existing_case": None,
            "prior_attempts": None,
        },
        "mentioned_documents": [],
        "documents_availability": "unknown",
        "ambiguity": {
            "present": False,
            "reason": None,
            "needs_confirmation": False,
            "alternative_area": None,
            "alternative_subject": None,
        },
        "urgency": "normal",
        "safety": {
            "level": "normal",
            "reason": "sem risco explícito",
            "detected_risks": [],
            "recommend_handoff": False,
        },
    }
    payload.update(overrides)
    return payload


CASES_SPEC: list[tuple[str, dict, dict, dict]] = []


def add(case_id: str, req: dict, exp: dict, synthetic: dict) -> None:
    CASES_SPEC.append((case_id, req, exp, synthetic))


# 1) Pedido explícito de humano vs genérico de advogado
add(
    "cmp_explicit_human",
    request(
        "cmp_explicit_human",
        [msg("m1", "Quero falar com um atendente humano agora, por favor")],
    ),
    expected(
        "cmp_explicit_human",
        intents=["new_legal_lead", "undetermined"],
        primary_areas=["undetermined", "other"],
        subjects=["undetermined", "other"],
        required_facts=["explicit_human_request"],
        required_fact_values={"explicit_human_request": ["true"]},
        annotation_rationale=(
            "Pedido direto de atendente/humano. Chave canônica true. "
            "Área/assunto podem ficar undetermined — o foco é o fato."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="undetermined",
            case_facts=[
                {
                    "key": "explicit_human_request",
                    "value": "true",
                    "certainty": "explicit",
                    "source_message_ids": ["m1"],
                    "from_trusted_crm_context": False,
                }
            ],
            reasoning_summary="Lead pede atendente humano de forma explícita",
        )
    },
)

add(
    "cmp_generic_lawyer",
    request(
        "cmp_generic_lawyer",
        [msg("m1", "Preciso de um advogado para me orientar sobre um problema")],
    ),
    expected(
        "cmp_generic_lawyer",
        intents=["new_legal_lead", "undetermined"],
        primary_areas=["undetermined", "other"],
        subjects=["undetermined", "other"],
        forbidden_fact_values={"explicit_human_request": ["true"]},
        annotation_rationale=(
            "Pedido genérico de advogado NÃO preenche explicit_human_request=true. "
            "Omitir a chave ou false sustentado; true é proibido."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="undetermined",
            reasoning_summary="Pedido genérico de orientação jurídica",
        )
    },
)

# 2) Cliente existente sem pedido explícito de humano
add(
    "cmp_existing_no_human",
    request(
        "cmp_existing_no_human",
        [msg("m1", "Já sou cliente do escritório e preciso de outra ajuda")],
    ),
    expected(
        "cmp_existing_no_human",
        intents=["existing_client_other_request", "existing_client_case_status"],
        primary_areas=["undetermined", "other"],
        subjects=["undetermined", "other"],
        forbidden_fact_values={"explicit_human_request": ["true"]},
        annotation_rationale=(
            "Ser cliente não prova pedido explícito de humano. Intent de cliente "
            "existente é aceitável; explicit_human_request=true é proibido."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="existing_client_other_request",
            procedural_situation={
                "stage": None,
                "prior_request": None,
                "prior_denial": None,
                "existing_case": True,
                "prior_attempts": None,
            },
            reasoning_summary="Lead declara ser cliente e pede outra ajuda",
        )
    },
)

# 3) CRM vazio vs CRM com fatos confiáveis
add(
    "cmp_crm_empty_fake_trust",
    request(
        "cmp_crm_empty_fake_trust",
        [msg("m1", "Meu marido foi preso ontem")],
    ),
    expected(
        "cmp_crm_empty_fake_trust",
        intents=["new_legal_lead"],
        primary_areas=["social_security", "criminal", "undetermined"],
        subjects=["prison_allowance", "flagrant_arrest", "undetermined"],
        annotation_rationale=(
            "known_facts vazio. Qualquer from_trusted_crm_context=true deve falhar "
            "na validação semântica (unverified_trusted_crm_context). A expectativa "
            "de classificação é ampla; o diagnóstico principal é a proveniência."
        ),
    ),
    {
        # Sintético propositalmente inválido: CRM trust sem suporte → fail-closed.
        "lead_understanding": base_understanding(
            intent="new_legal_lead",
            primary_area="social_security",
            subject="prison_allowance",
            subsubjects=["spouse_relationship"],
            case_facts=[
                {
                    "key": "detainee_imprisoned",
                    "value": "true",
                    "certainty": "explicit",
                    "source_message_ids": ["m1"],
                    "from_trusted_crm_context": True,
                }
            ],
            reasoning_summary="Relata prisão do cônjuge",
            safety={
                "level": "high",
                "reason": "prisão relatada",
                "detected_risks": ["arrest_or_detention"],
                "recommend_handoff": False,
            },
        )
    },
)

add(
    "cmp_crm_supported",
    request(
        "cmp_crm_supported",
        [msg("m1", "Quero continuar o atendimento")],
        known_facts=[
            {
                "key": "explicit_human_request",
                "value": "true",
                "certainty": "explicit",
                "source_message_ids": [],
                "from_trusted_crm_context": True,
            }
        ],
    ),
    expected(
        "cmp_crm_supported",
        intents=["new_legal_lead", "undetermined", "existing_client_other_request"],
        primary_areas=["undetermined", "other"],
        subjects=["undetermined", "other"],
        required_facts=["explicit_human_request"],
        required_fact_values={"explicit_human_request": ["true", "sim"]},
        annotation_rationale=(
            "CRM traz explicit_human_request=true com from_trusted_crm_context. "
            "Ecoar com o mesmo valor e flag CRM é válido; booleanos equivalentes ok."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="undetermined",
            case_facts=[
                {
                    "key": "explicit_human_request",
                    "value": "sim",
                    "certainty": "explicit",
                    "source_message_ids": [],
                    "from_trusted_crm_context": True,
                }
            ],
            reasoning_summary="Fato CRM de pedido humano suportado",
        )
    },
)

# 4) Correção de valor vs pessoas diferentes
add(
    "cmp_correction_single",
    request(
        "cmp_correction_single",
        [
            msg("m1", "Contribuí uns 3 anos para o INSS"),
            msg("m2", "Corrigindo: foram 7 anos de contribuição", reply_to="m1"),
        ],
    ),
    expected(
        "cmp_correction_single",
        intents=["new_legal_lead", "undetermined"],
        primary_areas=["social_security", "undetermined"],
        subjects=["retirement", "benefit_review", "undetermined"],
        required_fact_values={"contributed_years": ["7", "7 anos"]},
        forbidden_fact_values={"contributed_years": ["3", "3 anos"]},
        annotation_rationale=(
            "Correção explícita do mesmo alvo. O modelo deve emitir só o valor 7. "
            "Emitir 3 e 7 deixa a chave NÃO resolvida (fact_state.v2) e falha."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="new_legal_lead",
            primary_area="social_security",
            subject="retirement",
            case_facts=[
                {
                    "key": "contributed_years",
                    "value": "7",
                    "certainty": "explicit",
                    "source_message_ids": ["m2"],
                    "from_trusted_crm_context": False,
                }
            ],
            reasoning_summary="Lead corrige tempo de contribuição para 7 anos",
        )
    },
)

add(
    "cmp_different_people",
    request(
        "cmp_different_people",
        [
            msg("m1", "Minha mãe recebe o benefício há anos"),
            msg("m2", "Meu pai também recebe um benefício", reply_to="m1"),
        ],
    ),
    expected(
        "cmp_different_people",
        intents=["new_legal_lead", "undetermined"],
        primary_areas=["social_security", "undetermined", "family"],
        subjects=["undetermined", "other", "benefit_review"],
        annotation_rationale=(
            "Duas pessoas distintas; reply_to não é correção. Não colapsar em um "
            "único valor vigente. Sem chave canônica obrigatória — diagnóstico de "
            "não substituição indevida."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="new_legal_lead",
            primary_area="social_security",
            subject="undetermined",
            ambiguity={
                "present": True,
                "reason": "dois beneficiários distintos no relato",
                "needs_confirmation": True,
                "alternative_area": None,
                "alternative_subject": None,
            },
            reasoning_summary="Relato menciona mãe e pai como beneficiários distintos",
        )
    },
)

# 5) Prazo próximo sem matéria definida
add(
    "cmp_deadline_no_area",
    request(
        "cmp_deadline_no_area",
        [msg("m1", "Tenho uma audiência amanhã cedo e ainda não sei o que fazer")],
    ),
    expected(
        "cmp_deadline_no_area",
        intents=["new_legal_lead", "undetermined"],
        primary_areas=["undetermined", "other"],
        subjects=["undetermined", "other"],
        risks=["imminent_deadline"],
        annotation_rationale=(
            "Prazo próximo sem matéria jurídica definida. Área/assunto undetermined "
            "aceitáveis. Risco imminent_deadline no understanding é diagnóstico do "
            "modelo (não risco efetivo operacional)."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="undetermined",
            urgency="high",
            safety={
                "level": "high",
                "reason": "audiência amanhã sem matéria definida",
                "detected_risks": ["imminent_deadline"],
                "recommend_handoff": False,
            },
            reasoning_summary="Prazo próximo sem área jurídica clara",
        )
    },
)

# 6) Fraude explícita vs cobrança contestada
add(
    "cmp_explicit_fraud",
    request(
        "cmp_explicit_fraud",
        [
            msg(
                "m1",
                "Abriram um empréstimo no meu nome sem eu autorizar e sumiram com o dinheiro",
            )
        ],
    ),
    expected(
        "cmp_explicit_fraud",
        intents=["new_legal_lead"],
        primary_areas=["consumer"],
        subjects=["banking_fraud"],
        risks=["fraud_or_scam"],
        required_facts=["unauthorized_loan"],
        annotation_rationale=(
            "Empréstimo não autorizado explícito → banking_fraud e risco fraud_or_scam. "
            "Fato unauthorized_loan sustentado."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="new_legal_lead",
            primary_area="consumer",
            subject="banking_fraud",
            case_facts=[
                {
                    "key": "unauthorized_loan",
                    "value": "empréstimo aberto sem autorização",
                    "certainty": "explicit",
                    "source_message_ids": ["m1"],
                    "from_trusted_crm_context": False,
                }
            ],
            safety={
                "level": "high",
                "reason": "empréstimo não autorizado",
                "detected_risks": ["fraud_or_scam"],
                "recommend_handoff": False,
            },
            reasoning_summary="Relata empréstimo não autorizado em nome do lead",
        )
    },
)

add(
    "cmp_disputed_charge",
    request(
        "cmp_disputed_charge",
        [msg("m1", "A empresa está me cobrando uma dívida que eu discordo do valor")],
    ),
    expected(
        "cmp_disputed_charge",
        intents=["new_legal_lead"],
        primary_areas=["civil", "consumer"],
        subjects=[
            "debt_collection",
            "unauthorized_charge",
            "contract_breach",
            "other",
            "undetermined",
        ],
        forbidden_risks=["fraud_or_scam"],
        annotation_rationale=(
            "Cobrança contestada sem alegação de fraude. fraud_or_scam é proibido. "
            "Área civil ou consumer com subjects de cobrança/contrato aceitáveis."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="new_legal_lead",
            primary_area="civil",
            subject="debt_collection",
            reasoning_summary="Lead contesta valor de cobrança sem alegar fraude",
        )
    },
)

# 7) Múltiplas demandas jurídicas
add(
    "cmp_competing_demands",
    request(
        "cmp_competing_demands",
        [
            msg(
                "m1",
                "Fui demitido sem receber as verbas e também quero revisar meu benefício do INSS",
            )
        ],
    ),
    expected(
        "cmp_competing_demands",
        intents=["new_legal_lead"],
        primary_areas=["labor", "social_security"],
        subjects=[
            "unpaid_severance",
            "wage_claim",
            "dismissal_without_just_cause",
            "benefit_review",
            "undetermined",
        ],
        annotation_rationale=(
            "Duas demandas (trabalhista e previdenciária). primary/secondary ou "
            "ambiguity. Subject de uma demanda NÃO vira subsubject da outra. "
            "subsubjects=[] é válido."
        ),
    ),
    {
        "lead_understanding": base_understanding(
            intent="new_legal_lead",
            primary_area="labor",
            secondary_area="social_security",
            subject="unpaid_severance",
            subsubjects=[],
            ambiguity={
                "present": True,
                "reason": "demanda trabalhista e previdenciária concorrentes",
                "needs_confirmation": True,
                "alternative_area": "social_security",
                "alternative_subject": "benefit_review",
            },
            reasoning_summary="Duas demandas distintas: verbas rescisórias e revisão INSS",
        )
    },
)


def main() -> None:
    synthetic_map: dict[str, dict] = {}
    for case_id, req, exp, synth in CASES_SPEC:
        dump(CASES_DIR / f"{case_id}.json", {"case_id": case_id, "request": req})
        dump(EXPECTED_DIR / f"{case_id}.json", exp)
        synthetic_map[case_id] = synth
    dump(SYNTHETIC_DIR / "baseline_responses.json", synthetic_map)
    # Candidate: mesma qualidade na maioria; diverge em generic_lawyer (erro proposital)
    # e disputed_charge (marca fraude indevida) para exercitar o pareamento.
    candidate = json.loads(json.dumps(synthetic_map))
    candidate["cmp_generic_lawyer"] = {
        "lead_understanding": base_understanding(
            intent="undetermined",
            case_facts=[
                {
                    "key": "explicit_human_request",
                    "value": "true",
                    "certainty": "explicit",
                    "source_message_ids": ["m1"],
                    "from_trusted_crm_context": False,
                }
            ],
            reasoning_summary="Marca indevidamente pedido humano",
        )
    }
    candidate["cmp_disputed_charge"] = {
        "lead_understanding": base_understanding(
            intent="new_legal_lead",
            primary_area="civil",
            subject="debt_collection",
            safety={
                "level": "high",
                "reason": "cobrança tratada como fraude",
                "detected_risks": ["fraud_or_scam"],
                "recommend_handoff": False,
            },
            reasoning_summary="Marca fraude sem sustentação",
        )
    }
    dump(SYNTHETIC_DIR / "candidate_responses.json", candidate)
    print(f"generated {len(CASES_SPEC)} reserved compare cases → {CASES_DIR}")
    print(f"synthetic maps → {SYNTHETIC_DIR}")


if __name__ == "__main__":
    main()
