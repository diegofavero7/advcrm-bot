"""Validação e resolução de expectativas de avaliação (eval_expected.v5)."""

from __future__ import annotations

from typing import Any

from evaluations.metrics import NOT_APPLICABLE, NOT_OBSERVED

# Campos de caminho: não herdam o topo (evita regra certa no contexto errado).
PATH_SPECIFIC_KEYS = frozenset(
    {
        "acceptable_policy_rules",
        "acceptable_handoff_reasons",
        "acceptable_priorities",
        "require_model_inference_skipped",
        "handoff_required",
    }
)
# Herdam o topo quando o ramo não declara a chave (override explícito no ramo).
SHARED_KEYS = frozenset(
    {
        "extractor_cues",
        "risk_temporal",
        "required_extractor_risks",
    }
)


class ExpectationConfigError(ValueError):
    """Expectations inválidas — bloquear execução antes de inferência."""


def resolve_active_expectation_slice(
    expected: dict[str, Any],
    pipeline_status: str,
) -> dict[str, Any]:
    """Resolve fatia ativa.

    Sem ``path_expectations``: usa o topo (compatível com v3/v4).
    Com mapa:
    - campos de caminho (regras/motivo/prioridade) **não** herdam o topo;
    - campos compartilhados (cues/temporal/riscos do extrator) herdam o topo
      se o ramo **não declarar a chave** (declarar ``null`` ou ``[]`` é explícito).
    """
    path_map = expected.get("path_expectations")
    if not isinstance(path_map, dict) or not path_map:
        return {
            "acceptable_policy_rules": list(expected.get("acceptable_policy_rules") or []),
            "require_model_inference_skipped": expected.get("require_model_inference_skipped"),
            "acceptable_handoff_reasons": expected.get("acceptable_handoff_reasons"),
            "acceptable_priorities": expected.get("acceptable_priorities"),
            "handoff_required": expected.get("handoff_required"),
            "extractor_cues": expected.get("extractor_cues"),
            "risk_temporal": expected.get("risk_temporal"),
            "required_extractor_risks": expected.get("required_extractor_risks"),
            "path_branch": None,
            "path_expectation_missing": False,
            "shared_from_top": sorted(SHARED_KEYS),
        }

    branch = path_map.get(pipeline_status)
    if not isinstance(branch, dict):
        return {
            "acceptable_policy_rules": [],
            "require_model_inference_skipped": None,
            "acceptable_handoff_reasons": None,
            "acceptable_priorities": None,
            "handoff_required": expected.get("handoff_required"),
            "extractor_cues": expected.get("extractor_cues"),
            "risk_temporal": expected.get("risk_temporal"),
            "required_extractor_risks": expected.get("required_extractor_risks"),
            "path_branch": None,
            "path_expectation_missing": True,
            "shared_from_top": sorted(SHARED_KEYS),
        }

    shared_from_top: list[str] = []
    resolved: dict[str, Any] = {
        "acceptable_policy_rules": list(branch.get("acceptable_policy_rules") or []),
        "require_model_inference_skipped": branch.get(
            "require_model_inference_skipped",
            expected.get("require_model_inference_skipped"),
        ),
        "acceptable_handoff_reasons": branch.get("acceptable_handoff_reasons"),
        "acceptable_priorities": branch.get("acceptable_priorities"),
        "handoff_required": (
            branch["handoff_required"]
            if "handoff_required" in branch
            else expected.get("handoff_required")
        ),
        "path_branch": pipeline_status,
        "path_expectation_missing": False,
    }
    for key in SHARED_KEYS:
        if key in branch:
            resolved[key] = branch[key]
        else:
            resolved[key] = expected.get(key)
            if key in expected:
                shared_from_top.append(key)
    resolved["shared_from_top"] = shared_from_top
    return resolved


def _cue_annotation_present(cue_spec: object) -> bool:
    return isinstance(cue_spec, dict) and len(cue_spec) > 0


def _annotation_for_critical(
    name: str,
    *,
    expected: dict[str, Any],
    active: dict[str, Any],
) -> bool:
    if name == "action":
        return bool(expected.get("acceptable_actions"))
    if name == "handoff":
        return (
            active.get("handoff_required") is not None
            or expected.get("handoff_required") is not None
        )
    if name == "policy_rule":
        rules = list(active.get("acceptable_policy_rules") or [])
        return len(rules) > 0
    if name == "model_inference_skipped":
        return active.get("require_model_inference_skipped") is not None
    if name == "handoff_reason":
        return isinstance(active.get("acceptable_handoff_reasons"), list)
    if name == "priority":
        return isinstance(active.get("acceptable_priorities"), list)
    if name == "extractor_cues":
        return _cue_annotation_present(active.get("extractor_cues"))
    if name == "risk_temporal":
        spec = active.get("risk_temporal")
        return isinstance(spec, dict) and bool(spec)
    if name == "extractor_risks":
        val = active.get("required_extractor_risks")
        return isinstance(val, list) and len(val) > 0
    return name in {"fact_values", "required_facts"}


def validate_expectation_config(expected: dict[str, Any]) -> None:
    """Garante que checagens críticas têm anotação suficiente nos caminhos declarados."""
    case_id = expected.get("case_id") or "<unknown>"
    critical = list(expected.get("critical_expectations") or [])
    if not critical:
        _validate_cue_shapes(case_id, expected)
        return

    path_map = expected.get("path_expectations")
    if isinstance(path_map, dict) and path_map:
        statuses = [str(s) for s in path_map]
    else:
        statuses = ["__top__"]

    errors: list[str] = []
    for status in statuses:
        if status == "__top__":
            active = resolve_active_expectation_slice(expected, "success")
        else:
            active = resolve_active_expectation_slice(expected, status)
        for name in critical:
            if name in {"fact_values", "required_facts"}:
                continue
            if not _annotation_for_critical(name, expected=expected, active=active):
                errors.append(
                    f"{case_id}: critical '{name}' sem anotação suficiente (caminho={status})"
                )

    cue_errors = _validate_cue_shapes(case_id, expected)
    errors.extend(cue_errors)
    if errors:
        raise ExpectationConfigError("\n".join(errors))


def _validate_cue_shapes(case_id: str, expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    specs: list[dict[str, Any]] = []
    top = expected.get("extractor_cues")
    if isinstance(top, dict):
        specs.append(top)
    path_map = expected.get("path_expectations")
    if isinstance(path_map, dict):
        for branch in path_map.values():
            if isinstance(branch, dict) and isinstance(branch.get("extractor_cues"), dict):
                specs.append(branch["extractor_cues"])
    for cue in specs:
        for key in (
            "urgent_help_related_risks",
            "urgent_help_forbidden_related_risks",
            "immediate_danger_related_risks",
            "immediate_danger_forbidden_related_risks",
        ):
            if key in cue and cue[key] is not None and not isinstance(cue[key], list):
                errors.append(f"{case_id}: {key} deve ser lista (ou omitido)")
    return errors


def validate_all_expectations(expected_list: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    for exp in expected_list:
        try:
            validate_expectation_config(exp)
        except ExpectationConfigError as exc:
            errors.append(str(exc))
    if errors:
        raise ExpectationConfigError("\n".join(errors))


def related_risks_from_cue(
    safety_signals: dict[str, Any],
    cue_name: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Resolve related_occurrence_ids → riscos e ocorrências (sem colapsar IDs)."""
    occ_by_id = {
        str(o.get("occurrence_id")): o
        for o in (safety_signals.get("occurrences") or [])
        if isinstance(o, dict) and o.get("occurrence_id")
    }
    cue = safety_signals.get(cue_name) or {}
    related_ids = list(cue.get("related_occurrence_ids") or [])
    related_occs: list[dict[str, Any]] = []
    risks: list[str] = []
    for rid in related_ids:
        occ = occ_by_id.get(str(rid))
        if occ is None:
            continue
        related_occs.append(occ)
        risk = occ.get("risk")
        if risk:
            risks.append(str(risk))
    return risks, related_occs


def score_extractor_cues(
    *,
    cue_spec: dict[str, Any] | None,
    safety_signals: dict[str, Any] | None,
) -> tuple[bool | str, dict[str, Any]]:
    """Avalia cues v2 + associações exigidas/proibidas/ausência explícita."""
    if cue_spec is None:
        return NOT_APPLICABLE, {"reason": "cue_spec_absent"}
    if not isinstance(cue_spec, dict):
        return NOT_APPLICABLE, {"reason": "cue_spec_invalid"}
    if not cue_spec:
        return NOT_APPLICABLE, {"reason": "cue_spec_empty"}
    if not isinstance(safety_signals, dict):
        return NOT_OBSERVED, {"reason": "safety_signals_absent"}
    if safety_signals.get("schema_version") != "safety_signals.v2":
        return NOT_OBSERVED, {"reason": "safety_signals_not_v2"}

    details: dict[str, Any] = {}
    failures: list[str] = []

    for cue_name in ("urgent_help_request", "immediate_danger"):
        if cue_name not in cue_spec:
            continue
        want = cue_spec.get(cue_name)
        cue = safety_signals.get(cue_name) or {}
        observed = cue.get("state")
        details[cue_name] = {"expected": want, "observed": observed}
        if observed != want:
            failures.append(cue_name)

    for cue_name, req_key, forbid_key in (
        (
            "urgent_help_request",
            "urgent_help_related_risks",
            "urgent_help_forbidden_related_risks",
        ),
        (
            "immediate_danger",
            "immediate_danger_related_risks",
            "immediate_danger_forbidden_related_risks",
        ),
    ):
        related_risks, related_occs = related_risks_from_cue(safety_signals, cue_name)
        related_risk_set = set(related_risks)
        details[f"{cue_name}_related_occurrences"] = [
            {
                "occurrence_id": o.get("occurrence_id"),
                "risk": o.get("risk"),
                "assertion": o.get("assertion"),
                "temporal_context": o.get("temporal_context"),
            }
            for o in related_occs
        ]

        if req_key in cue_spec:
            required = cue_spec.get(req_key)
            if required is None:
                details[req_key] = {"expected": None, "observed": sorted(related_risk_set)}
            else:
                required_list = list(required or [])
                details[req_key] = {
                    "expected": required_list,
                    "observed": sorted(related_risk_set),
                    "explicit_empty": len(required_list) == 0,
                }
                if not required_list:
                    if related_risk_set:
                        failures.append(req_key)
                elif not set(required_list).issubset(related_risk_set):
                    failures.append(req_key)

        if forbid_key in cue_spec:
            forbidden = list(cue_spec.get(forbid_key) or [])
            hits = sorted(related_risk_set.intersection(forbidden))
            details[forbid_key] = {"forbidden": forbidden, "hits": hits}
            if hits:
                failures.append(forbid_key)

    return (len(failures) == 0), {"failures": failures, **details}


def score_risk_temporal(
    *,
    temporal_spec: dict[str, Any] | None,
    safety_signals: dict[str, Any] | None,
) -> tuple[bool | str, dict[str, Any]]:
    if temporal_spec is None:
        return NOT_APPLICABLE, {"reason": "temporal_spec_absent"}
    if not isinstance(temporal_spec, dict) or not temporal_spec:
        return NOT_APPLICABLE, {"reason": "temporal_spec_empty"}
    if not isinstance(safety_signals, dict):
        return NOT_OBSERVED, {"reason": "safety_signals_absent"}
    if safety_signals.get("schema_version") != "safety_signals.v2":
        return NOT_OBSERVED, {"reason": "safety_signals_not_v2"}

    by_risk: dict[str, list[str]] = {}
    for occ in safety_signals.get("occurrences") or []:
        if not isinstance(occ, dict):
            continue
        risk = occ.get("risk")
        temporal = occ.get("temporal_context")
        if risk and temporal:
            by_risk.setdefault(str(risk), []).append(str(temporal))

    details: dict[str, Any] = {}
    failures: list[str] = []
    missing_risks: list[str] = []
    wrong_temporal: list[str] = []
    for risk, allowed in temporal_spec.items():
        allowed_list = list(allowed or [])
        observed = by_risk.get(str(risk), [])
        entry: dict[str, Any] = {
            "expected_any_of": allowed_list,
            "observed": observed,
        }
        if not observed:
            entry["failure_kind"] = "missing_occurrence"
            missing_risks.append(str(risk))
            failures.append(str(risk))
        elif not any(t in allowed_list for t in observed):
            entry["failure_kind"] = "wrong_temporal"
            wrong_temporal.append(str(risk))
            failures.append(str(risk))
        else:
            entry["failure_kind"] = None
        details[str(risk)] = entry

    details["failures"] = failures
    details["missing_occurrences"] = missing_risks
    details["wrong_temporal"] = wrong_temporal
    # Uma ocorrência ausente não deve ser descrita só como temporalidade errada.
    details["summary"] = {
        "missing_occurrence_count": len(missing_risks),
        "wrong_temporal_count": len(wrong_temporal),
    }
    return (len(failures) == 0), details
