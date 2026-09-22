"""Resolução de política conservadora — obrigatório vs recomendação.

Precedência documentada (ramos de `decide_conservative_action` + guard):

Obrigatórias (resolvem o próximo passo; dispensam inferência de next_step):
1. existing_client_case_status
2. explicit_human_or_existing_client
3. flagrant_arrest_requires_handoff
4. imminent_deadline_requires_handoff  (sinais operacionais: só extractor-confirmados)
5. immediate_urgency_blocks_auto_route
6. domestic_violence_urgency_requires_handoff  (operacional + urgency high/immediate)
7. non_legal_or_spam
8. low_confidence
9. max_questions
10. undetermined_classification_requires_clarification
11. spam_legal_inconsistency_guard (ver spam_inconsistency_guard.v1; pós-decisão)

Recomendações (não substituem o modelo; apenas registradas):
- needs_clarification  (ambiguidade parcial / confiança média — NÃO cobre
  classificação totalmente indeterminada)
- high_confidence_route_candidate

O modelo não pode contrariar uma decisão obrigatória. Em conflito, a política
obrigatória prevalece. Recomendações não forçam override do modelo válido.
A reconciliação final também recusa `route_lead` com área e assunto
indeterminados, reutilizando a mesma resolução determinística e proveniência.

Sinais de risco para regras baseadas em risco vêm dos sinais **operacionais**
(`effective` = extrator confirmado OU taxonomia + fato explícito estruturado).
`imminent_deadline` operacional exige extrator (sem promoção taxonômica).
Flagrante continua por subject estruturado. Sem matching textual.
Composição permanece `effective_safety.v3` (agregação affirmed); a política passa a
`conservative_action.v7` pelas regras 6 e 10 e pelo consumo canônico do pedido
humano (domínio booleano validado + evidência em declaração do lead + chave
**resolvida**: valores divergentes na mesma chave não autorizam handoff).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.domain.enums import (
    FactCertainty,
    HandoffReason,
    Intent,
    LegalArea,
    Priority,
    RiskFlag,
    TriageAction,
)
from app.domain.fact_state import (
    FactStateResolution,
    fact_refs_from_facts,
    resolve_fact_state,
)
from app.domain.fact_vocabulary import EXPLICIT_HUMAN_REQUEST, canonical_boolean_is_true
from app.policies import (
    DOMESTIC_VIOLENCE_HANDOFF_FLAG,
    UNDETERMINED_CLARIFICATION_FLAG,
    build_undetermined_clarification_step,
    decide_conservative_action,
    is_fully_undetermined_classification,
    must_block_question_when_handoff_required,
)
from app.safety.compose import EffectiveSafetySignals
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep

POLICY_VERSION = "conservative_action.v7"
SPAM_INCONSISTENCY_GUARD_VERSION = "spam_inconsistency_guard.v1"
SPAM_INCONSISTENCY_GUARD_FLAG = "spam_legal_inconsistency_guard"

MANDATORY_POLICY_FLAGS: frozenset[str] = frozenset(
    {
        "existing_client_case_status",
        "explicit_human_or_existing_client",
        "flagrant_arrest_requires_handoff",
        "imminent_deadline_requires_handoff",
        "immediate_urgency_blocks_auto_route",
        DOMESTIC_VIOLENCE_HANDOFF_FLAG,
        "non_legal_or_spam",
        "low_confidence",
        "max_questions",
        UNDETERMINED_CLARIFICATION_FLAG,
        SPAM_INCONSISTENCY_GUARD_FLAG,
    }
)

ADVISORY_POLICY_FLAGS: frozenset[str] = frozenset(
    {
        "needs_clarification",
        "high_confidence_route_candidate",
    }
)

_INDETERMINATE_AREAS = frozenset({LegalArea.OTHER, LegalArea.UNDETERMINED})
_INDETERMINATE_SUBJECTS = frozenset({"other", "undetermined"})


@dataclass(frozen=True)
class PolicyResolution:
    """Resultado da política sobre entendimento já validado."""

    decision: TriageNextStep
    mandatory: bool
    policy_version: str
    policy_rule_id: str
    policy_flags: tuple[str, ...]
    advisory_flags: tuple[str, ...]
    skips_model_inference: bool


def primary_rule_id(flags: list[str]) -> str:
    for flag in flags:
        if flag in MANDATORY_POLICY_FLAGS or flag in ADVISORY_POLICY_FLAGS:
            return flag
    return flags[0] if flags else "unspecified"


# Alias interno preservado para consumidores existentes.
_primary_rule_id = primary_rule_id


def is_mandatory_policy_decision(step: TriageNextStep) -> bool:
    return any(flag in MANDATORY_POLICY_FLAGS for flag in step.policy_flags)


def has_specific_legal_classification(understanding: LeadUnderstanding) -> bool:
    """Sinais estruturados de classificação jurídica específica (não texto livre)."""
    if understanding.primary_area in _INDETERMINATE_AREAS:
        return False
    return understanding.subject not in _INDETERMINATE_SUBJECTS


def spam_conflicts_with_legal_signals(understanding: LeadUnderstanding) -> bool:
    """intent=spam + classificação jurídica concreta = inconsistência.

    Não usa matching por palavra-chave no texto. Menção temática com
    area/subject indeterminados NÃO dispara o guard.
    """
    if understanding.intent != Intent.SPAM:
        return False
    return has_specific_legal_classification(understanding)


def apply_spam_inconsistency_guard(
    understanding: LeadUnderstanding,
    decision: TriageNextStep,
) -> tuple[TriageNextStep, str | None]:
    """Se ignore por spam conflita com sinais jurídicos, revisa internamente.

    Retorna (decisão, policy_version_override|None).
    """
    if decision.action != TriageAction.IGNORE:
        return decision, None
    if understanding.intent != Intent.SPAM:
        return decision, None
    if not spam_conflicts_with_legal_signals(understanding):
        return decision, None
    guarded = TriageNextStep(
        action=TriageAction.REQUEST_HUMAN_REVIEW,
        priority=Priority.HIGH,
        missing_information=["classification_consistency"],
        selected_missing_information=["classification_consistency"],
        proposed_question=None,
        requires_human_handoff=True,
        handoff_reason=HandoffReason.CONFLICTING_INFORMATION,
        policy_flags=[SPAM_INCONSISTENCY_GUARD_FLAG],
    )
    return guarded, SPAM_INCONSISTENCY_GUARD_VERSION


def case_fact_state(understanding: LeadUnderstanding) -> FactStateResolution:
    """Estado factual de `case_facts`: chaves resolvidas vs não resolvidas.

    Não elege vencedor entre valores divergentes — o contrato v1 não representa
    correção nem alvo factual. Ver `app/domain/fact_state.py`.
    """
    return resolve_fact_state(fact_refs_from_facts(list(understanding.case_facts)))


def crm_fact_state(request: TriageAnalysisRequest) -> FactStateResolution:
    """Mesmo tratamento para `known_facts` do CRM (autoridade distinta do lead)."""
    return resolve_fact_state(fact_refs_from_facts(list(request.known_facts)))


def detect_explicit_human_request(
    request: TriageAnalysisRequest,
    understanding: LeadUnderstanding,
) -> bool:
    """Sinal estruturado **inequívoco** de pedido explícito de atendente humano.

    Fontes (sem matching textual no lead, sem resumo do modelo):

    1. ``known_facts`` do CRM: ``key=explicit_human_request``,
       ``from_trusted_crm_context=true`` e valor booleano canônico verdadeiro;
    2. ``understanding.case_facts``: mesma chave, ``certainty=explicit``,
       ``source_message_ids`` não vazios e todos apontando para declarações
       textuais do lead.

    A chave precisa estar **resolvida** na sua fonte: se a mesma chave aparecer com
    valores divergentes (ex.: ``true`` e ``false``), o estado é NÃO RESOLVIDO e não
    autoriza handoff. Fato conflitante não é autorização — não se escolhe o mais
    recente nem o "mais forte". O CRM é avaliado primeiro por ser autoridade
    distinta; conflito dentro do CRM também não autoriza e não é resolvido pelo lead.

    Domínio fechado: ``true|1|yes|sim`` é verdadeiro, ``false|0|no|nao|não`` é
    falso e qualquer outro literal é inválido — nunca verdadeiro por truthiness.
    Pedido genérico de advogado, pedido de andamento, negação, fala citada de
    terceiros e instrução maliciosa não preenchem a chave. Intent indeterminado
    ou área/subject undetermined NÃO neutralizam o sinal.
    """
    crm_state = crm_fact_state(request)
    if crm_state.is_unresolved(EXPLICIT_HUMAN_REQUEST):
        return False
    crm_value = crm_state.resolved_value(EXPLICIT_HUMAN_REQUEST)
    if crm_value is not None and canonical_boolean_is_true(EXPLICIT_HUMAN_REQUEST, crm_value):
        trusted = any(
            known.key == EXPLICIT_HUMAN_REQUEST and known.from_trusted_crm_context
            for known in request.known_facts
        )
        if trusted:
            return True

    state = case_fact_state(understanding)
    if state.is_unresolved(EXPLICIT_HUMAN_REQUEST):
        return False
    value = state.resolved_value(EXPLICIT_HUMAN_REQUEST)
    if value is None or not canonical_boolean_is_true(EXPLICIT_HUMAN_REQUEST, value):
        return False

    lead_ids = frozenset(request.lead_declaration_message_ids())
    return any(
        case_fact.key == EXPLICIT_HUMAN_REQUEST
        and case_fact.certainty == FactCertainty.EXPLICIT
        and case_fact.source_message_ids
        and all(mid in lead_ids for mid in case_fact.source_message_ids)
        for case_fact in understanding.case_facts
    )


def resolve_conservative_policy(
    understanding: LeadUnderstanding,
    *,
    request: TriageAnalysisRequest,
    questions_asked: int = 0,
    settings: Settings | None = None,
    effective_safety: EffectiveSafetySignals | None = None,
) -> PolicyResolution:
    explicit = detect_explicit_human_request(request, understanding)
    effective_risks: frozenset[RiskFlag] | None = None
    if effective_safety is not None:
        effective_risks = effective_safety.effective_flags()
    decision = decide_conservative_action(
        understanding,
        explicit_human_request=explicit,
        questions_asked=questions_asked,
        settings=settings,
        effective_risks=effective_risks,
    )
    decision, guard_version = apply_spam_inconsistency_guard(understanding, decision)
    flags = tuple(decision.policy_flags)
    mandatory = is_mandatory_policy_decision(decision)
    advisory = tuple(f for f in flags if f in ADVISORY_POLICY_FLAGS)
    version = guard_version or POLICY_VERSION
    return PolicyResolution(
        decision=decision,
        mandatory=mandatory,
        policy_version=version,
        policy_rule_id=_primary_rule_id(list(flags)),
        policy_flags=flags,
        advisory_flags=advisory,
        skips_model_inference=mandatory,
    )


def model_route_lead_is_fully_undetermined(
    understanding: LeadUnderstanding | None,
    model_proposal: TriageNextStep,
) -> bool:
    """`route_lead` sem área nem assunto não tem destino — recusado na reconciliação."""
    if understanding is None:
        return False
    if model_proposal.action != TriageAction.ROUTE_LEAD:
        return False
    return is_fully_undetermined_classification(understanding)


def reconcile_next_step(
    *,
    policy: PolicyResolution,
    model_proposal: TriageNextStep | None,
    understanding: LeadUnderstanding | None = None,
) -> tuple[TriageNextStep, str]:
    """Retorna (decisão efetiva, source).

    source: deterministic_policy | model

    Defesa em profundidade: mesmo com política apenas recomendativa, `route_lead`
    totalmente indeterminado é substituído pela mesma resolução determinística da
    regra ``undetermined_classification_requires_clarification`` (mesma
    proveniência, sem segundo mecanismo).
    """
    if policy.mandatory:
        return policy.decision, "deterministic_policy"
    if model_proposal is None:
        raise ValueError("proposta do modelo ausente com política apenas recomendativa")
    if must_block_question_when_handoff_required(
        model_proposal.requires_human_handoff, model_proposal.action
    ):
        raise ValueError("handoff obrigatório impede ask_question")
    if model_route_lead_is_fully_undetermined(understanding, model_proposal):
        return build_undetermined_clarification_step(), "deterministic_policy"
    return model_proposal, "model"


def model_conflicts_with_mandatory(
    policy: PolicyResolution, model_proposal: TriageNextStep
) -> bool:
    """True se a proposta do modelo contradiz decisão obrigatória."""
    if not policy.mandatory:
        return False
    pol = policy.decision
    if pol.requires_human_handoff and model_proposal.action == TriageAction.ASK_QUESTION:
        return True
    if pol.action != model_proposal.action:
        return True
    return pol.requires_human_handoff != model_proposal.requires_human_handoff


def intent_implies_mandatory_without_risk_signal(understanding: LeadUnderstanding) -> bool:
    """Helper de documentação/teste: intents que resolvem sem depender de risks."""
    return understanding.intent in {
        Intent.EXISTING_CLIENT_CASE_STATUS,
        Intent.EXISTING_CLIENT_OTHER_REQUEST,
        Intent.SPAM,
        Intent.NON_LEGAL_CONTACT,
    }
