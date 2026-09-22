"""Isolamento de expected e fidelidade do exemplo 01 (auxílio-reclusão)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from app.application.context_builder import (
    build_next_step_context,
    build_understanding_context,
)
from app.cli.triage import _extract_request, _offline_understanding
from app.clients.ai_runtime import AiRuntimeClient
from app.clients.schemas import get_lead_understanding_schema
from app.config import Settings
from app.prompts import clear_prompt_cache, load_lead_understanding_prompt
from app.schemas.lead_understanding import CaseFact, LeadUnderstanding
from app.schemas.proposal import NextStepOnlyEnvelope
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_01 = ROOT / "examples/valid/01_prison_allowance_spouse.json"

MARKER_UNDERSTANDING = "LEAK_MARKER_EXPECTED_UNDERSTANDING_7f3a9c"
MARKER_NEXT_STEP = "LEAK_MARKER_EXPECTED_NEXT_STEP_4b2e1d"
MARKER_DOC = "LEAK_MARKER_FAKE_CARTEIRA_DOC"


def _load_example() -> dict[str, object]:
    return json.loads(EXAMPLE_01.read_text(encoding="utf-8"))


def test_example_01_expected_matches_two_messages_only() -> None:
    data = _load_example()
    request = data["request"]
    assert isinstance(request, dict)
    messages = request["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 2
    assert messages[0]["text"] == "Meu marido foi preso e preciso de auxílio-reclusão"
    assert messages[1]["text"] == "Ele tinha emprego registrado e temos um filho menor"

    understanding = LeadUnderstanding.model_validate(data["lead_understanding"])
    assert understanding.primary_area.value == "social_security"
    assert understanding.subject == "prison_allowance"
    assert set(understanding.subsubjects) == {"spouse_relationship", "child_dependent"}
    assert understanding.documents_availability.value == "unknown"
    assert understanding.mentioned_documents == []
    assert understanding.procedural_situation.existing_case is None
    assert understanding.procedural_situation.prior_request is None
    assert understanding.procedural_situation.prior_denial is None
    assert "violence_or_threat" not in {r.value for r in understanding.safety.detected_risks}

    by_key = {f.key: f for f in understanding.case_facts}
    assert set(by_key) == {
        "relationship_to_detainee",
        "detainee_imprisoned",
        "employment_registered",
        "minor_child_dependent",
    }
    assert by_key["relationship_to_detainee"].source_message_ids == ["m1"]
    assert by_key["detainee_imprisoned"].source_message_ids == ["m1"]
    assert by_key["employment_registered"].source_message_ids == ["m2"]
    assert by_key["minor_child_dependent"].source_message_ids == ["m2"]
    for fact in understanding.case_facts:
        assert "inferred_from_context" not in fact.value.lower()
        assert "carteira" not in fact.value.lower()


def test_understanding_only_extracts_request_not_expected() -> None:
    data = _load_example()
    poisoned = copy.deepcopy(data)
    lu = poisoned["lead_understanding"]
    assert isinstance(lu, dict)
    lu["reasoning_summary"] = MARKER_UNDERSTANDING
    lu["mentioned_documents"] = [{"label": MARKER_DOC, "availability": "available"}]
    ns = poisoned["triage_next_step"]
    assert isinstance(ns, dict)
    ns["proposed_question"] = MARKER_NEXT_STEP

    request = _extract_request(poisoned)
    assert all(MARKER_UNDERSTANDING not in (m.text or "") for m in request.messages)
    assert all(MARKER_NEXT_STEP not in (m.text or "") for m in request.messages)
    assert all(MARKER_DOC not in (m.text or "") for m in request.messages)

    clear_prompt_cache()
    prompt = load_lead_understanding_prompt()
    context = build_understanding_context(request)
    schema = get_lead_understanding_schema()
    client = AiRuntimeClient(Settings(ai_runtime_enabled=False, ai_runtime_max_tokens=2048))
    payload = client.build_structured_payload(
        json_schema=schema,
        messages=[
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": context},
        ],
    )
    sent = json.dumps(payload["messages"], ensure_ascii=False)
    assert MARKER_UNDERSTANDING not in sent
    assert MARKER_NEXT_STEP not in sent
    assert MARKER_DOC not in sent
    assert "Meu marido foi preso" in sent
    assert "emprego registrado" in sent
    assert "taxonomy_catalog" in sent
    # Expected do exemplo não entra no contexto de compreensão
    assert '"lead_understanding"' not in context
    assert '"triage_next_step"' not in context
    # Documento inventado no expected não vaza; menção no prompt v3 é regra negativa ok
    assert MARKER_DOC not in context
    assert "carteira de trabalho" not in context


def test_offline_cli_understanding_payload_excludes_expected_markers(
    tmp_path: Path,
) -> None:
    data = _load_example()
    poisoned = copy.deepcopy(data)
    lu = poisoned["lead_understanding"]
    assert isinstance(lu, dict)
    lu["reasoning_summary"] = MARKER_UNDERSTANDING
    ns = poisoned["triage_next_step"]
    assert isinstance(ns, dict)
    ns["proposed_question"] = MARKER_NEXT_STEP
    path = tmp_path / "poisoned.json"
    path.write_text(json.dumps(poisoned, ensure_ascii=False), encoding="utf-8")

    clear_prompt_cache()
    request = _extract_request(json.loads(path.read_text(encoding="utf-8")))
    offline = _offline_understanding(request)
    blob = json.dumps(offline, ensure_ascii=False)
    assert MARKER_UNDERSTANDING not in blob
    assert MARKER_NEXT_STEP not in blob
    assert offline["prompt_version"] == "lead_understanding.v9"


def test_next_step_only_legitimately_includes_validated_understanding() -> None:
    data = _load_example()
    lu = copy.deepcopy(data["lead_understanding"])
    assert isinstance(lu, dict)
    lu["reasoning_summary"] = MARKER_UNDERSTANDING
    understanding = LeadUnderstanding.model_validate(lu)
    request = _extract_request(data)
    context = build_next_step_context(
        request=request,
        understanding=understanding,
        playbook_id="playbook_prison_allowance",
        playbook_version="v1",
        questions_asked=0,
        last_bot_question=None,
        missing_information=["approximate_prison_date"],
    )
    assert MARKER_UNDERSTANDING in context
    assert '"lead_understanding"' in context

    envelope = NextStepOnlyEnvelope.model_validate(
        {
            "request": data["request"],
            "lead_understanding": lu,
            "questions_asked": 0,
            "last_bot_question": None,
            "missing_information": ["approximate_prison_date"],
        }
    )
    assert envelope.lead_understanding.reasoning_summary == MARKER_UNDERSTANDING


def test_case_fact_rejects_known_placeholders_deterministically() -> None:
    with pytest.raises(ValidationError) as caught:
        CaseFact(
            key="relationship_to_detainee",
            value="inferred_from_context",
            certainty="explicit",  # type: ignore[arg-type]
            source_message_ids=["m1"],
            from_trusted_crm_context=False,
        )
    assert any(e["type"] == "case_fact_placeholder_value" for e in caught.value.errors())


def test_prompt_v4_fact_fidelity_rules() -> None:
    clear_prompt_cache()
    prompt = load_lead_understanding_prompt()
    assert prompt.version == "lead_understanding.v9"
    text = prompt.text
    assert "opções PERMITIDAS" in text or "opções permitidas" in text.lower()
    assert "inferred_from_context" in text
    assert "não equivale a false" in text.lower() or "não equivale a false" in text
    assert "carteira de trabalho" in text
    assert "violence_or_threat" in text
    assert "elegibilidade" in text.lower()
    assert "Meu marido foi preso e preciso de auxílio-reclusão" not in text
    assert "Ele tinha emprego registrado e temos um filho menor" not in text


def test_prompt_v7_deadline_without_matter_rules() -> None:
    clear_prompt_cache()
    prompt = load_lead_understanding_prompt()
    assert prompt.version == "lead_understanding.v9"
    text = prompt.text
    assert "depois de amanhã" in text
    assert "prison_date" in text
    assert "NÃO implica prisão" in text or "não implica prisão" in text.lower()
    assert "Tenho audiência amanhã cedo e preciso de representação" not in text


def test_eval_ss_prison_allowance_is_distinct_one_message_case() -> None:
    """Cópia em evaluations não é o exemplo 01; expectativas batem com a entrada."""
    case = json.loads(
        (ROOT / "evaluations/cases/ss_prison_allowance.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        (ROOT / "evaluations/expected/ss_prison_allowance.json").read_text(encoding="utf-8")
    )
    messages = case["request"]["messages"]
    assert len(messages) == 1
    assert "cônjuge" in messages[0]["text"].lower() or "conjuge" in messages[0]["text"].lower()
    assert "auxílio-reclusão" in messages[0]["text"] or "auxilio-reclusao" in messages[0]["text"]
    assert expected["acceptable_subjects"] == ["prison_allowance"]
    assert "relationship_to_detainee" in expected["required_fact_keys"]
    assert "arrest_or_detention" in expected["required_risks"]
    # Não exige documentos/carteira/existing_case inventados
    assert "carteira" not in json.dumps(expected, ensure_ascii=False).lower()
