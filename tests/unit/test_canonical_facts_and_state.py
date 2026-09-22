"""Domínio de valores canônicos, placeholders e estado factual resolvido/não resolvido.

Cobre os defeitos de `spam` (fatos preenchidos sem sustentação), `undetermined`
(texto livre em chave booleana), `existing_status` (pedido humano fabricado) e
`correction_later` (valores divergentes de `contributed_years` sem evidência de
correção no contrato).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.clients.validation_diagnostics import (
    is_semantic_coherence_failure,
    sanitize_validation_error,
)
from app.domain.enums import (
    ContentType,
    FactCertainty,
    Intent,
    LegalArea,
    MessageDirection,
    MessageRole,
    RiskFlag,
    TriageAction,
    TriageState,
    UrgencyLevel,
)
from app.domain.fact_state import CONTRACT_GAP, FACT_STATE_VERSION
from app.domain.fact_vocabulary import (
    DETAINEE_IMPRISONED,
    EXPLICIT_HUMAN_REQUEST,
    canonical_boolean_is_true,
    canonical_fact_value_out_of_domain,
    parse_canonical_boolean,
)
from app.policies.resolution import (
    case_fact_state,
    crm_fact_state,
    detect_explicit_human_request,
    resolve_conservative_policy,
)
from app.safety.promotion import find_supporting_fact
from app.schemas.inbound import ConversationMessage, KnownFact, TriageAnalysisRequest
from app.schemas.lead_understanding import (
    Ambiguity,
    CaseFact,
    LeadUnderstanding,
    ProceduralSituation,
    SafetyAssessment,
)
from pydantic import ValidationError


def _message(
    message_id: str,
    *,
    text: str = "mensagem",
    role: MessageRole = MessageRole.LEAD,
    reply_to: str | None = None,
) -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        role=role,
        direction=(
            MessageDirection.INBOUND if role == MessageRole.LEAD else MessageDirection.OUTBOUND
        ),
        content_type=ContentType.TEXT,
        text=text,
        created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
        reply_to_message_id=reply_to,
    )


def _request(
    messages: list[ConversationMessage] | None = None,
    *,
    known: list[KnownFact] | None = None,
) -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e1",
        tenant_id="t1",
        lead_id="l1",
        conversation_id="c1",
        triage_state=TriageState.PENDING_CLASSIFICATION,
        source="whatsapp",
        messages=messages or [_message("m1")],
        known_facts=known or [],
        previous_decision=None,
    )


def _understanding(**overrides: object) -> LeadUnderstanding:
    base: dict[str, object] = {
        "schema_version": "lead_understanding.v1",
        "intent": Intent.NEW_LEGAL_LEAD,
        "language": "pt-BR",
        "primary_area": LegalArea.UNDETERMINED,
        "secondary_area": None,
        "subject": "undetermined",
        "subsubjects": [],
        "confidence": 0.9,
        "reasoning_summary": "resumo curto",
        "participants": [],
        "case_facts": [],
        "procedural_situation": ProceduralSituation(
            stage=None,
            prior_request=None,
            prior_denial=None,
            existing_case=None,
            prior_attempts=None,
        ),
        "mentioned_documents": [],
        "documents_availability": "unknown",
        "ambiguity": Ambiguity(
            present=False,
            reason=None,
            needs_confirmation=False,
            alternative_area=None,
            alternative_subject=None,
        ),
        "urgency": UrgencyLevel.NORMAL,
        "safety": SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="sem risco",
            detected_risks=[],
            recommend_handoff=False,
        ),
    }
    base.update(overrides)
    return LeadUnderstanding.model_validate(base)


def _fact(key: str, value: str, **kwargs: object) -> CaseFact:
    payload: dict[str, object] = {
        "key": key,
        "value": value,
        "certainty": FactCertainty.EXPLICIT,
        "source_message_ids": ["m1"],
        "from_trusted_crm_context": False,
    }
    payload.update(kwargs)
    return CaseFact.model_validate(payload)


# --- 7/8. Pedido humano canônico ---


def test_canonical_true_triggers_human_request_rule() -> None:
    understanding = _understanding(case_facts=[_fact(EXPLICIT_HUMAN_REQUEST, "true")])
    request = _request()
    assert detect_explicit_human_request(request, understanding) is True
    resolution = resolve_conservative_policy(understanding, request=request)
    assert resolution.policy_rule_id == "explicit_human_or_existing_client"
    assert resolution.decision.action == TriageAction.HUMAN_HANDOFF


@pytest.mark.parametrize("literal", ["false", "0", "no", "nao", "não"])
def test_falsy_literals_are_not_truthy(literal: str) -> None:
    assert parse_canonical_boolean(literal) is False
    assert canonical_boolean_is_true(EXPLICIT_HUMAN_REQUEST, literal) is False
    understanding = _understanding(case_facts=[_fact(EXPLICIT_HUMAN_REQUEST, literal)])
    assert detect_explicit_human_request(_request(), understanding) is False


# --- 9. Texto livre em chave booleana é inválido ---


@pytest.mark.parametrize(
    "value",
    ["preciso de um advogado", "marido foi preso", "talvez", "sim, por favor"],
)
def test_free_text_in_boolean_key_is_rejected(value: str) -> None:
    assert canonical_fact_value_out_of_domain(EXPLICIT_HUMAN_REQUEST, value) is True
    with pytest.raises(ValidationError) as caught:
        _fact(EXPLICIT_HUMAN_REQUEST, value)
    assert any(e["type"] == "canonical_boolean_fact_value" for e in caught.value.errors())


def test_invalid_boolean_value_is_not_renamed_or_dropped() -> None:
    """Fail-closed: valor inválido rejeita o payload, não vira `false` nem some."""
    with pytest.raises(ValidationError):
        _understanding(case_facts=[_fact(DETAINEE_IMPRISONED, "marido foi preso")])


def test_invalid_boolean_value_is_diagnosed_as_semantic_coherence() -> None:
    with pytest.raises(ValidationError) as caught:
        _fact(DETAINEE_IMPRISONED, "marido foi preso")
    details = sanitize_validation_error(caught.value)
    assert is_semantic_coherence_failure(details) is True
    for item in details:
        assert set(item.keys()) == {"loc", "type"}
    assert "marido foi preso" not in str(details)


# --- 10/11. Advogado genérico e status de cliente ---


def test_generic_lawyer_request_is_not_a_human_request() -> None:
    understanding = _understanding(
        case_facts=[_fact("legal_help_request", "procura escritorio para representacao")]
    )
    assert detect_explicit_human_request(_request(), understanding) is False


def test_existing_client_status_handoff_without_fabricated_human_request() -> None:
    understanding = _understanding(intent=Intent.EXISTING_CLIENT_CASE_STATUS)
    request = _request()
    assert detect_explicit_human_request(request, understanding) is False
    resolution = resolve_conservative_policy(understanding, request=request)
    assert resolution.policy_rule_id == "existing_client_case_status"
    assert resolution.decision.requires_human_handoff is True


def test_third_party_quote_without_lead_source_is_not_a_human_request() -> None:
    """Fato apoiado só em mensagem de agente/system não é declaração do lead."""
    messages = [_message("m1"), _message("m2", role=MessageRole.AGENT)]
    understanding = _understanding(
        case_facts=[_fact(EXPLICIT_HUMAN_REQUEST, "true", source_message_ids=["m2"])]
    )
    assert detect_explicit_human_request(_request(messages), understanding) is False


def test_conflicting_human_request_values_do_not_authorize_handoff() -> None:
    """Fato conflitante não é autorização inequívoca — nem o mais recente vence."""
    messages = [_message("m1"), _message("m2", reply_to="m1")]
    understanding = _understanding(
        case_facts=[
            _fact(EXPLICIT_HUMAN_REQUEST, "false", source_message_ids=["m1"]),
            _fact(EXPLICIT_HUMAN_REQUEST, "true", source_message_ids=["m2"]),
        ]
    )
    request = _request(messages)
    assert case_fact_state(understanding).is_unresolved(EXPLICIT_HUMAN_REQUEST) is True
    assert detect_explicit_human_request(request, understanding) is False
    resolution = resolve_conservative_policy(understanding, request=request)
    assert resolution.policy_rule_id != "explicit_human_or_existing_client"


def test_conflicting_human_request_order_does_not_matter() -> None:
    messages = [_message("m1"), _message("m2", reply_to="m1")]
    understanding = _understanding(
        case_facts=[
            _fact(EXPLICIT_HUMAN_REQUEST, "true", source_message_ids=["m1"]),
            _fact(EXPLICIT_HUMAN_REQUEST, "false", source_message_ids=["m2"]),
        ]
    )
    assert detect_explicit_human_request(_request(messages), understanding) is False


def test_conflicting_crm_human_request_is_not_resolved_by_lead_fact() -> None:
    request = _request(
        known=[
            KnownFact(
                key=EXPLICIT_HUMAN_REQUEST,
                value="true",
                certainty=FactCertainty.EXPLICIT,
                source_message_ids=[],
                from_trusted_crm_context=True,
            ),
            KnownFact(
                key=EXPLICIT_HUMAN_REQUEST,
                value="false",
                certainty=FactCertainty.EXPLICIT,
                source_message_ids=[],
                from_trusted_crm_context=True,
            ),
        ]
    )
    understanding = _understanding(
        case_facts=[_fact(EXPLICIT_HUMAN_REQUEST, "true", source_message_ids=["m1"])]
    )
    assert crm_fact_state(request).is_unresolved(EXPLICIT_HUMAN_REQUEST) is True
    assert detect_explicit_human_request(request, understanding) is False


def test_conflicting_detainee_imprisoned_does_not_promote_risk() -> None:
    """Promoção taxonômica também não elege valor entre fatos divergentes."""
    conflicting = [
        _fact(DETAINEE_IMPRISONED, "true", source_message_ids=["m1"]),
        _fact(DETAINEE_IMPRISONED, "false", source_message_ids=["m2"]),
    ]
    assert (
        find_supporting_fact(
            subject="prison_allowance",
            risk=RiskFlag.ARREST_OR_DETENTION,
            case_facts=conflicting,
            allowed_message_ids=frozenset({"m1", "m2"}),
        )
        is None
    )
    single = [_fact(DETAINEE_IMPRISONED, "true", source_message_ids=["m1"])]
    assert (
        find_supporting_fact(
            subject="prison_allowance",
            risk=RiskFlag.ARREST_OR_DETENTION,
            case_facts=single,
            allowed_message_ids=frozenset({"m1"}),
        )
        is not None
    )


def test_crm_trusted_fact_still_triggers_without_source_messages() -> None:
    request = _request(
        known=[
            KnownFact(
                key=EXPLICIT_HUMAN_REQUEST,
                value="sim",
                certainty=FactCertainty.EXPLICIT,
                source_message_ids=[],
                from_trusted_crm_context=True,
            )
        ]
    )
    assert detect_explicit_human_request(request, _understanding()) is True


# --- 12/13. Placeholders vs enums legítimos ---


@pytest.mark.parametrize(
    "placeholder",
    [
        "unknown",
        "desconhecido",
        "not_specified",
        "não informado",
        "n/a",
        "null",
        "inferred_from_context",
        "lead_untrusted_data",
    ],
)
def test_placeholder_values_are_rejected_in_case_facts(placeholder: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _fact("relationship_to_detainee", placeholder)
    assert any(e["type"] == "case_fact_placeholder_value" for e in caught.value.errors())


def test_unknown_remains_valid_in_contract_enums() -> None:
    """`unknown` é legítimo em documents_availability — proibição é só em case_facts."""
    understanding = _understanding(documents_availability="unknown")
    assert understanding.documents_availability == "unknown"


# --- 14/15. Estado factual: resolvido vs NÃO resolvido ---


def test_single_value_is_resolved() -> None:
    """Caminho de sucesso: o modelo entrega só o valor vigente."""
    understanding = _understanding(
        case_facts=[_fact("contributed_years", "5", source_message_ids=["m2"])]
    )
    resolution = case_fact_state(understanding)
    assert resolution.resolved_value("contributed_years") == "5"
    assert resolution.is_unresolved("contributed_years") is False
    assert resolution.conflicts == ()


def test_conflicting_values_stay_unresolved_without_winner() -> None:
    understanding = _understanding(
        case_facts=[
            _fact("contributed_years", "2", source_message_ids=["m1"]),
            _fact("contributed_years", "5", source_message_ids=["m2"]),
        ]
    )
    resolution = case_fact_state(understanding)
    assert resolution.is_unresolved("contributed_years") is True
    assert resolution.resolved_value("contributed_years") is None
    assert set(resolution.values_for("contributed_years")) == {"2", "5"}


def test_reply_chain_is_not_evidence_of_correction() -> None:
    """`reply_to_message_id` é contexto de resposta, não sinal de correção."""
    messages = [_message("m1"), _message("m2", reply_to="m1")]
    understanding = _understanding(
        case_facts=[
            _fact("contributed_years", "2", source_message_ids=["m1"]),
            _fact("contributed_years", "5", source_message_ids=["m2"]),
        ]
    )
    request = _request(messages)
    assert request.messages[1].reply_to_message_id == "m1"
    resolution = case_fact_state(understanding)
    assert resolution.is_unresolved("contributed_years") is True
    assert resolution.resolved_value("contributed_years") is None


def test_no_fact_is_removed_by_resolution() -> None:
    facts = [
        _fact("contributed_years", "2", source_message_ids=["m1"]),
        _fact("contributed_years", "5", source_message_ids=["m2"]),
    ]
    understanding = _understanding(case_facts=facts)
    resolution = case_fact_state(understanding)
    assert len(resolution.facts) == 2
    assert [f.value for f in understanding.case_facts] == ["2", "5"]


def test_duplicate_equal_values_are_not_a_conflict() -> None:
    understanding = _understanding(
        case_facts=[
            _fact("relationship_to_detainee", "conjuge", source_message_ids=["m1"]),
            _fact("relationship_to_detainee", " Conjuge ", source_message_ids=["m2"]),
        ]
    )
    resolution = case_fact_state(understanding)
    assert resolution.is_unresolved("relationship_to_detainee") is False
    assert resolution.resolved_value("relationship_to_detainee") == "conjuge"


def test_equivalent_boolean_literals_are_not_a_conflict() -> None:
    understanding = _understanding(
        case_facts=[
            _fact(EXPLICIT_HUMAN_REQUEST, "true", source_message_ids=["m1"]),
            _fact(EXPLICIT_HUMAN_REQUEST, "sim", source_message_ids=["m2"]),
        ]
    )
    resolution = case_fact_state(understanding)
    assert resolution.is_unresolved(EXPLICIT_HUMAN_REQUEST) is False


def test_fact_state_version_is_v2() -> None:
    assert FACT_STATE_VERSION == "fact_state.v2"
    assert "não representa correção" in CONTRACT_GAP


# --- Regressões: mesma chave, mensagens encadeadas, alvos diferentes ---


@pytest.mark.parametrize(
    ("key", "first", "second", "scenario"),
    [
        ("relationship_to_detainee", "conjuge", "irmao", "pessoas diferentes"),
        ("contributed_years", "5", "12", "períodos diferentes"),
        ("prison_circumstances", "flagrante", "mandado judicial", "eventos diferentes"),
    ],
)
def test_chained_messages_about_different_targets_are_not_collapsed(
    key: str, first: str, second: str, scenario: str
) -> None:
    """Encadeamento não autoriza escolher um alvo: ambos ficam, chave não resolvida."""
    messages = [_message("m1"), _message("m2", reply_to="m1")]
    understanding = _understanding(
        case_facts=[
            _fact(key, first, source_message_ids=["m1"]),
            _fact(key, second, source_message_ids=["m2"]),
        ]
    )
    request = _request(messages)
    resolution = case_fact_state(understanding)
    assert resolution.is_unresolved(key) is True, scenario
    assert resolution.resolved_value(key) is None, scenario
    assert set(resolution.values_for(key)) == {first, second}, scenario
    assert len(request.messages) == 2


def test_reply_that_adds_information_does_not_discard_the_previous_fact() -> None:
    """Chaves distintas: acréscimo não é correção e nada fica não resolvido."""
    messages = [_message("m1"), _message("m2", reply_to="m1")]
    understanding = _understanding(
        case_facts=[
            _fact("contributed_years", "6", source_message_ids=["m1"]),
            _fact("contribution_period", "2018-2024", source_message_ids=["m2"]),
        ]
    )
    resolution = case_fact_state(_request(messages) and understanding)
    assert resolution.conflicts == ()
    assert resolution.resolved_value("contributed_years") == "6"
    assert resolution.resolved_value("contribution_period") == "2018-2024"


def test_contract_has_no_representation_for_explicit_correction() -> None:
    """Bloqueio documentado: nenhum campo de `CaseFact` expressa correção ou alvo.

    Enquanto for assim, não existe caminho de substituição determinística. Se o
    contrato ganhar essa representação, este teste falha e obriga a revisão da regra.
    """
    fields = set(CaseFact.model_fields)
    assert fields == {
        "key",
        "value",
        "certainty",
        "source_message_ids",
        "from_trusted_crm_context",
    }
    correction_fields = {"supersedes", "corrects", "target_id", "valid_from", "revision"}
    assert fields.isdisjoint(correction_fields)
