#!/usr/bin/env python3
"""Gera exemplos anonimizados válidos e inválidos para a Fase 1."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALID = ROOT / "examples" / "valid"
INVALID = ROOT / "examples" / "invalid"


def dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def msg(
    mid: str,
    text: str,
    *,
    role: str = "lead",
    direction: str = "inbound",
    content_type: str = "text",
    reply_to: str | None = None,
    created_at: str = "2026-09-20T14:00:00-03:00",
) -> dict:
    return {
        "message_id": mid,
        "role": role,
        "direction": direction,
        "content_type": content_type,
        "text": text if content_type == "text" else None,
        "created_at": created_at,
        "reply_to_message_id": reply_to,
    }


def base_request(messages: list[dict], **kwargs: object) -> dict:
    payload = {
        "event_id": "evt-001",
        "tenant_id": "tenant-demo",
        "lead_id": "lead-001",
        "conversation_id": "conv-001",
        "triage_state": "pending_classification",
        "source": "whatsapp",
        "messages": messages,
        "known_facts": [],
        "previous_decision": None,
    }
    payload.update(kwargs)
    return payload


def understanding(**overrides: object) -> dict:
    base = {
        "schema_version": "lead_understanding.v1",
        "intent": "new_legal_lead",
        "language": "pt-BR",
        "primary_area": "social_security",
        "secondary_area": None,
        "subject": "prison_allowance",
        "subsubjects": ["spouse_relationship", "child_dependent"],
        "confidence": 0.91,
        "reasoning_summary": (
            "Relata prisão do marido, pede auxílio-reclusão; "
            "menciona emprego registrado e filho menor"
        ),
        "participants": [
            {"role": "contact_person", "relationship": "spouse"},
            {"role": "affected_person", "relationship": "detainee"},
            {"role": "dependent", "relationship": "minor_child"},
        ],
        "case_facts": [
            {
                "key": "relationship_to_detainee",
                "value": "cônjuge (marido)",
                "certainty": "explicit",
                "source_message_ids": ["m1"],
                "from_trusted_crm_context": False,
            },
            {
                "key": "detainee_imprisoned",
                "value": "marido foi preso",
                "certainty": "explicit",
                "source_message_ids": ["m1"],
                "from_trusted_crm_context": False,
            },
            {
                "key": "employment_registered",
                "value": "emprego registrado mencionado",
                "certainty": "explicit",
                "source_message_ids": ["m2"],
                "from_trusted_crm_context": False,
            },
            {
                "key": "minor_child_dependent",
                "value": "filho menor",
                "certainty": "explicit",
                "source_message_ids": ["m2"],
                "from_trusted_crm_context": False,
            },
        ],
        "procedural_situation": {
            "stage": None,
            "prior_request": None,
            "prior_denial": None,
            # null = desconhecimento (não use false como “não informado”)
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
            "reason": (
                "Prisão relatada e dependente menor; sem ameaça ou urgência imediata adicionais"
            ),
            "detected_risks": ["arrest_or_detention", "child_or_vulnerable_person"],
            "recommend_handoff": False,
        },
    }
    base.update(overrides)
    return base


def next_step(**overrides: object) -> dict:
    base = {
        "schema_version": "triage_next_step.v1",
        "action": "ask_question",
        "priority": "normal",
        "missing_information": ["approximate_prison_date"],
        "selected_missing_information": ["approximate_prison_date"],
        "proposed_question": "Em que período aproximado ocorreu a prisão?",
        "requires_human_handoff": False,
        "handoff_reason": None,
        "policy_flags": ["playbook_prison_allowance"],
    }
    base.update(overrides)
    return base


def main() -> None:
    # 1. auxílio-reclusão com cônjuge, dependente e emprego
    dump(
        VALID / "01_prison_allowance_spouse.json",
        {
            "request": base_request(
                [
                    msg("m1", "Meu marido foi preso e preciso de auxílio-reclusão"),
                    msg("m2", "Ele tinha emprego registrado e temos um filho menor"),
                ]
            ),
            "lead_understanding": understanding(),
            "triage_next_step": next_step(),
        },
    )

    # 2. auxílio-reclusão incerto
    dump(
        VALID / "02_prison_allowance_uncertain.json",
        {
            "request": base_request(
                [msg("m1", "Acho que posso pedir aquele benefício da prisão, não sei o nome")]
            ),
            "lead_understanding": understanding(
                confidence=0.55,
                subject="prison_allowance",
                subsubjects=["undetermined"],
                reasoning_summary="Menciona benefício ligado a prisão sem nomear auxílio-reclusão",
                ambiguity={
                    "present": True,
                    "reason": "Benefício não nomeado com certeza",
                    "needs_confirmation": True,
                    "alternative_area": None,
                    "alternative_subject": "undetermined",
                },
                case_facts=[
                    {
                        "key": "benefit_uncertain",
                        "value": "benefício relacionado a prisão, nome incerto",
                        "certainty": "uncertain",
                        "source_message_ids": ["m1"],
                        "from_trusted_crm_context": False,
                    }
                ],
            ),
            "triage_next_step": next_step(
                action="request_human_review",
                proposed_question=None,
                requires_human_handoff=True,
                handoff_reason="low_confidence",
                missing_information=["benefit_confirmation"],
                selected_missing_information=["benefit_confirmation"],
                policy_flags=["low_confidence"],
            ),
        },
    )

    # 3. mensagens fragmentadas
    dump(
        VALID / "03_fragmented_messages.json",
        {
            "request": base_request(
                [
                    msg(
                        "m1",
                        "ele contribuiu acho que 2 anos",
                        created_at="2026-09-20T14:00:00-03:00",
                    ),
                    msg(
                        "m2",
                        "a 3",
                        reply_to="m1",
                        created_at="2026-09-20T14:00:12-03:00",
                    ),
                ]
            ),
            "note": "Fragmentos preservados com IDs distintos; não concatenar eliminando ordem",
        },
    )

    # 4. veículo financiamento
    dump(
        VALID / "04_vehicle_financing_discrepancy.json",
        {
            "request": base_request(
                [
                    msg(
                        "m1",
                        "Comprei um carro e o valor do financiamento veio diferente do combinado",
                    ),
                    msg("m2", "Foi em loja, não particular"),
                ]
            ),
            "lead_understanding": understanding(
                primary_area="consumer",
                subject="vehicle_purchase_irregularities",
                subsubjects=["financing_institution", "contract_values"],
                reasoning_summary="Relata divergência de valor no financiamento de veículo em loja",
                participants=[{"role": "potential_claimant", "relationship": None}],
                case_facts=[
                    {
                        "key": "seller_type",
                        "value": "loja",
                        "certainty": "explicit",
                        "source_message_ids": ["m2"],
                        "from_trusted_crm_context": False,
                    }
                ],
                safety={
                    "level": "normal",
                    "reason": "Sem risco imediato informado",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            ),
            "triage_next_step": next_step(
                missing_information=["purchase_date"],
                selected_missing_information=["purchase_date"],
                proposed_question="Em que data aproximada ocorreu a compra?",
                policy_flags=["playbook_vehicle"],
            ),
        },
    )

    # 5. omissão de leilão
    dump(
        VALID / "05_undisclosed_auction.json",
        {
            "request": base_request(
                [
                    msg(
                        "m1",
                        "Descobri depois que o carro tinha passagem por leilão e não me avisaram",
                    )
                ]
            ),
            "lead_understanding": understanding(
                primary_area="consumer",
                subject="undisclosed_auction_history",
                subsubjects=[],
                reasoning_summary="Relata omissão de passagem por leilão na compra do veículo",
                participants=[{"role": "potential_claimant", "relationship": None}],
                case_facts=[
                    {
                        "key": "auction_history",
                        "value": "passagem por leilão omitida",
                        "certainty": "explicit",
                        "source_message_ids": ["m1"],
                        "from_trusted_crm_context": False,
                    }
                ],
                safety={
                    "level": "normal",
                    "reason": "Sem risco imediato",
                    "detected_risks": ["fraud_or_scam"],
                    "recommend_handoff": False,
                },
            ),
        },
    )

    # 6. demissão sem verbas
    dump(
        VALID / "06_unpaid_severance.json",
        {
            "request": base_request([msg("m1", "Fui demitido e não recebi as verbas rescisórias")]),
            "lead_understanding": understanding(
                primary_area="labor",
                subject="unpaid_severance",
                subsubjects=[],
                reasoning_summary="Relata demissão sem pagamento de verbas rescisórias",
                participants=[{"role": "potential_claimant", "relationship": None}],
                case_facts=[
                    {
                        "key": "labor_issue",
                        "value": "verbas rescisórias não pagas",
                        "certainty": "explicit",
                        "source_message_ids": ["m1"],
                        "from_trusted_crm_context": False,
                    }
                ],
                safety={
                    "level": "normal",
                    "reason": "Sem risco imediato",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            ),
        },
    )

    # 7. guarda / pensão
    dump(
        VALID / "07_custody_or_support.json",
        {
            "request": base_request(
                [msg("m1", "Preciso tratar da guarda do meu filho e da pensão alimentícia")]
            ),
            "lead_understanding": understanding(
                primary_area="family",
                subject="child_custody",
                secondary_area=None,
                subsubjects=[],
                confidence=0.72,
                reasoning_summary="Menciona guarda de filho e pensão alimentícia",
                ambiguity={
                    "present": True,
                    "reason": "Guarda e alimentos mencionados juntos",
                    "needs_confirmation": True,
                    "alternative_area": "family",
                    "alternative_subject": "child_support",
                },
                participants=[
                    {"role": "contact_person", "relationship": "parent"},
                    {"role": "dependent", "relationship": "minor_child"},
                ],
                case_facts=[
                    {
                        "key": "family_issue",
                        "value": "guarda e pensão alimentícia",
                        "certainty": "explicit",
                        "source_message_ids": ["m1"],
                        "from_trusted_crm_context": False,
                    }
                ],
                safety={
                    "level": "normal",
                    "reason": "Envolve menor, sem violência relatada",
                    "detected_risks": ["child_or_vulnerable_person"],
                    "recommend_handoff": False,
                },
            ),
        },
    )

    # 8. cobrança civil
    dump(
        VALID / "08_civil_debt_collection.json",
        {
            "request": base_request(
                [msg("m1", "Estou sendo cobrado por uma dívida que não reconheço")]
            ),
            "lead_understanding": understanding(
                primary_area="civil",
                subject="debt_collection",
                subsubjects=[],
                reasoning_summary="Relata cobrança de dívida não reconhecida",
                participants=[{"role": "potential_claimant", "relationship": None}],
                case_facts=[
                    {
                        "key": "civil_issue",
                        "value": "cobrança de dívida contestada",
                        "certainty": "explicit",
                        "source_message_ids": ["m1"],
                        "from_trusted_crm_context": False,
                    }
                ],
                safety={
                    "level": "normal",
                    "reason": "Sem risco imediato",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            ),
        },
    )

    # 9. prisão/violência → handoff
    dump(
        VALID / "09_violence_handoff.json",
        {
            "request": base_request(
                [msg("m1", "Ele me ameaçou e estou com medo agora, preciso de ajuda urgente")]
            ),
            "lead_understanding": understanding(
                primary_area="family",
                subject="domestic_violence",
                subsubjects=[],
                confidence=0.88,
                reasoning_summary="Relata ameaça e medo com pedido de ajuda urgente",
                urgency="immediate",
                participants=[{"role": "affected_person", "relationship": None}],
                case_facts=[
                    {
                        "key": "threat",
                        "value": "ameaça relatada",
                        "certainty": "explicit",
                        "source_message_ids": ["m1"],
                        "from_trusted_crm_context": False,
                    }
                ],
                safety={
                    "level": "immediate",
                    "reason": "Ameaça e medo com urgência",
                    "detected_risks": ["violence_or_threat", "domestic_violence"],
                    "recommend_handoff": True,
                },
            ),
            "triage_next_step": next_step(
                action="human_handoff",
                priority="critical",
                proposed_question=None,
                requires_human_handoff=True,
                handoff_reason="immediate_risk",
                missing_information=[],
                selected_missing_information=[],
                policy_flags=["immediate_urgency_blocks_auto_route"],
            ),
        },
    )

    # 10. cliente existente andamento
    dump(
        VALID / "10_existing_client_case_status.json",
        {
            "request": base_request(
                [msg("m1", "Sou cliente e quero saber o andamento do meu processo")]
            ),
            "lead_understanding": understanding(
                intent="existing_client_case_status",
                primary_area="undetermined",
                subject="undetermined",
                subsubjects=[],
                confidence=0.9,
                reasoning_summary="Cliente existente solicita andamento processual",
                participants=[{"role": "contact_person", "relationship": "existing_client"}],
                case_facts=[
                    {
                        "key": "case_status_request",
                        "value": "pedido de andamento",
                        "certainty": "explicit",
                        "source_message_ids": ["m1"],
                        "from_trusted_crm_context": False,
                    }
                ],
                safety={
                    "level": "normal",
                    "reason": "Sem risco imediato",
                    "detected_risks": [],
                    "recommend_handoff": True,
                },
            ),
            "triage_next_step": next_step(
                action="human_handoff",
                priority="critical",
                proposed_question=None,
                requires_human_handoff=True,
                handoff_reason="case_status_request",
                missing_information=[],
                selected_missing_information=[],
                policy_flags=["existing_client_case_status"],
            ),
        },
    )

    # 11. não jurídico
    dump(
        VALID / "11_non_legal.json",
        {
            "request": base_request([msg("m1", "Qual o horário de funcionamento do escritório?")]),
            "lead_understanding": understanding(
                intent="non_legal_contact",
                primary_area="other",
                subject="other",
                subsubjects=[],
                confidence=0.95,
                reasoning_summary="Pergunta administrativa sobre horário, sem demanda jurídica",
                participants=[{"role": "contact_person", "relationship": None}],
                case_facts=[],
                safety={
                    "level": "normal",
                    "reason": "Sem risco",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            ),
        },
    )

    # 12. spam
    dump(
        VALID / "12_spam.json",
        {
            "request": base_request([msg("m1", "GANHE DINHEIRO RÁPIDO CLIQUE AQUI!!!")]),
            "lead_understanding": understanding(
                intent="spam",
                primary_area="other",
                subject="other",
                subsubjects=[],
                confidence=0.99,
                reasoning_summary="Mensagem promocional típica de spam",
                participants=[{"role": "unknown", "relationship": None}],
                case_facts=[],
                safety={
                    "level": "normal",
                    "reason": "Spam sem risco pessoal",
                    "detected_risks": ["fraud_or_scam"],
                    "recommend_handoff": False,
                },
            ),
            "triage_next_step": next_step(
                action="ignore",
                priority="low",
                proposed_question=None,
                requires_human_handoff=False,
                handoff_reason=None,
                missing_information=[],
                selected_missing_information=[],
                policy_flags=["non_legal_or_spam"],
            ),
        },
    )

    # 13. área indeterminada
    dump(
        VALID / "13_undetermined_area.json",
        {
            "request": base_request([msg("m1", "Tenho um problema e preciso de um advogado")]),
            "lead_understanding": understanding(
                intent="undetermined",
                primary_area="undetermined",
                subject="undetermined",
                subsubjects=[],
                confidence=0.4,
                reasoning_summary="Solicita advogado sem descrever a matéria",
                participants=[{"role": "contact_person", "relationship": None}],
                case_facts=[],
                ambiguity={
                    "present": True,
                    "reason": "Área jurídica não informada",
                    "needs_confirmation": True,
                    "alternative_area": None,
                    "alternative_subject": None,
                },
                safety={
                    "level": "normal",
                    "reason": "Sem risco imediato",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            ),
        },
    )

    # 14. duas áreas concorrentes
    dump(
        VALID / "14_competing_areas.json",
        {
            "request": base_request(
                [
                    msg(
                        "m1",
                        "Fui demitido e também quero revisar meu benefício "
                        "do INSS porque me acidentarei no trabalho",
                    )
                ]
            ),
            "lead_understanding": understanding(
                primary_area="labor",
                secondary_area="social_security",
                subject="workplace_accident",
                subsubjects=[],
                confidence=0.68,
                reasoning_summary=(
                    "Relata demissão e possível benefício previdenciário por acidente"
                ),
                ambiguity={
                    "present": True,
                    "reason": "Matérias trabalhista e previdenciária concorrentes",
                    "needs_confirmation": True,
                    "alternative_area": "social_security",
                    "alternative_subject": "accident_benefit",
                },
                participants=[{"role": "potential_claimant", "relationship": None}],
                case_facts=[
                    {
                        "key": "competing_matters",
                        "value": "demissão e benefício por acidente",
                        "certainty": "explicit",
                        "source_message_ids": ["m1"],
                        "from_trusted_crm_context": False,
                    }
                ],
                safety={
                    "level": "normal",
                    "reason": "Sem risco imediato",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            ),
        },
    )

    # 15. pedido explícito de atendente
    dump(
        VALID / "15_explicit_human_request.json",
        {
            "request": base_request(
                [msg("m1", "Quero falar com um atendente humano agora, por favor")]
            ),
            "lead_understanding": understanding(
                intent="undetermined",
                primary_area="undetermined",
                subject="undetermined",
                subsubjects=[],
                confidence=0.85,
                reasoning_summary="Solicitação explícita de atendimento humano",
                participants=[{"role": "contact_person", "relationship": None}],
                case_facts=[],
                safety={
                    "level": "normal",
                    "reason": "Pedido de humano sem risco adicional",
                    "detected_risks": [],
                    "recommend_handoff": True,
                },
            ),
            "triage_next_step": next_step(
                action="human_handoff",
                priority="high",
                proposed_question=None,
                requires_human_handoff=True,
                handoff_reason="user_requested_human",
                missing_information=[],
                selected_missing_information=[],
                policy_flags=["explicit_human_or_existing_client"],
            ),
        },
    )

    # 16. áudio não processado
    dump(
        VALID / "16_audio_unsupported.json",
        {
            "request": base_request(
                [
                    {
                        "message_id": "m1",
                        "role": "lead",
                        "direction": "inbound",
                        "content_type": "audio",
                        "text": None,
                        "created_at": "2026-09-20T14:00:00-03:00",
                        "reply_to_message_id": None,
                    },
                    msg("m2", "Mandei um áudio explicando o caso do carro"),
                ]
            ),
            "note": (
                "Áudio não processado; texto auxiliar permite continuar ou revisão se indispensável"
            ),
        },
    )

    # --- inválidos ---
    dump(
        INVALID / "extra_property_request.json",
        {
            **base_request([msg("m1", "olá")]),
            "hack_field": "not_allowed",
        },
    )
    dump(
        INVALID / "empty_messages.json",
        base_request([]),
    )
    # Fix empty messages - base_request with empty will fail min_length; write raw
    dump(
        INVALID / "empty_messages.json",
        {
            "event_id": "evt-x",
            "tenant_id": "t",
            "lead_id": "l",
            "conversation_id": "c",
            "triage_state": "pending_classification",
            "source": "whatsapp",
            "messages": [],
            "known_facts": [],
            "previous_decision": None,
        },
    )
    dump(
        INVALID / "duplicate_message_ids.json",
        base_request([msg("m1", "a"), msg("m1", "b")]),
    )
    dump(
        INVALID / "naive_datetime.json",
        {
            "event_id": "evt-x",
            "tenant_id": "t",
            "lead_id": "l",
            "conversation_id": "c",
            "triage_state": "pending_classification",
            "source": "whatsapp",
            "messages": [
                {
                    "message_id": "m1",
                    "role": "lead",
                    "direction": "inbound",
                    "content_type": "text",
                    "text": "olá",
                    "created_at": "2026-09-20T14:00:00",
                    "reply_to_message_id": None,
                }
            ],
            "known_facts": [],
            "previous_decision": None,
        },
    )
    dump(
        INVALID / "text_without_body.json",
        {
            "event_id": "evt-x",
            "tenant_id": "t",
            "lead_id": "l",
            "conversation_id": "c",
            "triage_state": "pending_classification",
            "source": "whatsapp",
            "messages": [
                {
                    "message_id": "m1",
                    "role": "lead",
                    "direction": "inbound",
                    "content_type": "text",
                    "text": None,
                    "created_at": "2026-09-20T14:00:00-03:00",
                    "reply_to_message_id": None,
                }
            ],
            "known_facts": [],
            "previous_decision": None,
        },
    )
    dump(
        INVALID / "invalid_reply_to.json",
        base_request([msg("m1", "olá", reply_to="m999")]),
    )
    dump(
        INVALID / "secondary_equals_primary.json",
        understanding(secondary_area="social_security"),
    )
    dump(
        INVALID / "ask_question_without_text.json",
        next_step(proposed_question=None),
    )
    dump(
        INVALID / "handoff_without_reason.json",
        next_step(
            action="human_handoff",
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=None,
        ),
    )
    dump(
        INVALID / "subject_area_mismatch.json",
        understanding(primary_area="labor", subject="prison_allowance", subsubjects=[]),
    )
    dump(
        INVALID / "merit_policy_flag.json",
        next_step(
            action="route_lead",
            proposed_question=None,
            policy_flags=["win_probability"],
        ),
    )

    print("examples generated")


if __name__ == "__main__":
    main()
