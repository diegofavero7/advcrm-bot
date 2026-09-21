"""Runner de avaliação — live opcional; offline/mock para CI."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluations.metrics import (
    STRUCTURAL_TARGET_KEYS,
    compute_metrics,
    evaluate_targets,
    render_report,
)

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = Path(__file__).resolve().parent / "cases"
EXPECTED_DIR = Path(__file__).resolve().parent / "expected"


def load_cases() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for case_path in sorted(CASES_DIR.glob("*.json")):
        expected_path = EXPECTED_DIR / case_path.name
        if not expected_path.is_file():
            raise FileNotFoundError(f"expected ausente: {expected_path}")
        case = json.loads(case_path.read_text(encoding="utf-8"))
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        pairs.append((case, expected))
    return pairs


def score_case(
    *,
    expected: dict[str, Any],
    understanding: dict[str, Any] | None,
    next_step: dict[str, Any] | None,
    fail_closed: bool,
    latency_ms: float | None,
    retry_count: int,
    json_valid: bool,
    schema_valid: bool,
) -> dict[str, Any]:
    intent = understanding.get("intent") if understanding else None
    area = understanding.get("primary_area") if understanding else None
    subject = understanding.get("subject") if understanding else None
    action = next_step.get("action") if next_step else None
    requires_handoff = next_step.get("requires_human_handoff") if next_step else None
    risks = set()
    if understanding and isinstance(understanding.get("safety"), dict):
        risks = set(understanding["safety"].get("detected_risks") or [])

    fact_keys = set()
    if understanding:
        for fact in understanding.get("case_facts") or []:
            if isinstance(fact, dict) and "key" in fact:
                fact_keys.add(fact["key"])

    invented = any(k in fact_keys for k in (expected.get("forbidden_fact_keys") or []))
    required_facts = set(expected.get("required_fact_keys") or [])
    required_facts_match = required_facts.issubset(fact_keys) if required_facts else True

    required_risks = set(expected.get("required_risks") or [])
    risks_match = required_risks.issubset(risks) if required_risks else True

    handoff_expected = expected.get("handoff_required")
    handoff_match = True
    if handoff_expected is True:
        handoff_match = requires_handoff is True or (
            action in {"human_handoff", "request_human_review"}
        )

    return {
        "case_id": expected.get("case_id"),
        "json_valid": json_valid,
        "schema_valid": schema_valid,
        "intent_match": intent in (expected.get("acceptable_intents") or []) if intent else False,
        "area_match": area in (expected.get("acceptable_primary_areas") or []) if area else False,
        "subject_match": (
            not expected.get("acceptable_subjects")
            or subject in (expected.get("acceptable_subjects") or [])
        )
        if subject
        else not expected.get("acceptable_subjects"),
        "expected_handoff": handoff_expected,
        "handoff_match": handoff_match,
        "required_risks": list(required_risks),
        "risks_match": risks_match,
        "invented_facts": invented,
        "required_facts_match": required_facts_match,
        "repeated_question": False,
        "fail_closed": fail_closed,
        "latency_ms": latency_ms,
        "retry_count": retry_count,
        "predicted_area": area,
        "predicted_intent": intent,
        "predicted_subject": subject,
        "predicted_action": action,
    }


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
            latency = None
            retries = 0
            if proposal.metadata.understanding_latency_ms is not None:
                latency = proposal.metadata.understanding_latency_ms
            if proposal.metadata.next_step_latency_ms is not None:
                latency = (latency or 0) + proposal.metadata.next_step_latency_ms
            retries = (proposal.metadata.understanding_retry_count or 0) + (
                proposal.metadata.next_step_retry_count or 0
            )
            fail_closed = proposal.status == ProposalStatus.FAILED_CLOSED
            results.append(
                score_case(
                    expected=expected,
                    understanding=understanding,
                    next_step=next_step,
                    fail_closed=fail_closed,
                    latency_ms=latency,
                    retry_count=retries,
                    json_valid=not fail_closed or understanding is not None,
                    schema_valid=understanding is not None
                    and (next_step is not None or fail_closed),
                )
            )
    return results


def run_offline_structure_check(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Valida estrutura dos casos sem chamar runtime (CI)."""
    from app.schemas.inbound import TriageAnalysisRequest

    results: list[dict[str, Any]] = []
    for case, expected in pairs:
        json_valid = True
        schema_valid = True
        try:
            TriageAnalysisRequest.model_validate(case["request"])
        except Exception:
            json_valid = False
            schema_valid = False
        results.append(
            score_case(
                expected=expected,
                understanding=None,
                next_step=None,
                fail_closed=False,
                latency_ms=0.0,
                retry_count=0,
                json_valid=json_valid,
                schema_valid=schema_valid,
            )
        )
    return results


def write_artifacts(
    results: list[dict[str, Any]],
    *,
    mode: str,
) -> Path:
    metrics = compute_metrics(results, mode=mode)
    checks = evaluate_targets(metrics, mode=mode)
    if mode == "live":
        passed: bool | None = all(value is True for value in checks.values())
    else:
        passed = all(checks[key] is True for key in STRUCTURAL_TARGET_KEYS)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "artifacts" / "evaluations" / f"{stamp}_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "passed_targets": passed,
        "checks": checks,
        "metrics": metrics,
        "results": results,
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(
        render_report(metrics, checks, mode=mode),
        encoding="utf-8",
    )
    return out_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runner de avaliação AdvCRM Bot")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Executa contra runtime real (não usar no CI)",
    )
    args = parser.parse_args(argv)
    pairs = load_cases()
    if args.live:
        results = asyncio.run(run_live(pairs))
        out = write_artifacts(results, mode="live")
        print(out)
        metrics = compute_metrics(results, mode="live")
        checks = evaluate_targets(metrics, mode="live")
        return 0 if all(value is True for value in checks.values()) else 4
    results = run_offline_structure_check(pairs)
    out = write_artifacts(results, mode="offline")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
