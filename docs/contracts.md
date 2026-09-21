# Contratos

Todos os modelos usam `extra="forbid"`. Campos anuláveis são `T | null` e aparecem em `required` no JSON Schema (nunca omitidos).

## TriageAnalysisRequest

Entrada autorizada pelo AdvCRM.

- `source`: enum fechado (`whatsapp`, `crm_manual`, `system`, `import`, `other`, `undetermined`)
- `known_facts`: lista de `KnownFact` (não dict livre)
- `previous_decision`: `PreviousDecisionSummary | null`
- Mensagens: IDs únicos; `content_type=text` exige texto; `created_at` timezone-aware; `reply_to_message_id` deve existir

## lead_understanding.v1

Compreensão estruturada: intent, área, assunto, fatos rastreáveis, ambiguidade, urgência/segurança.

`reasoning_summary` resume sinais observáveis — **não** é cadeia de pensamento.

## triage_next_step.v1

Próxima ação proposta. Regras:

- `ask_question` ⇔ pergunta não vazia
- handoff ⇔ `handoff_reason`
- urgência imediata não gera roteamento automático normal
- proibido flags de mérito (`win_probability`, etc.)

## JSON Schema e advcrm-ai (futuro)

Schemas exportados por `scripts/export_json_schemas.py` serão usados em:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "lead_understanding.v1",
      "strict": true,
      "schema": { }
    }
  }
}
```

Validação fail-closed no bot. A chamada HTTP **não** está implementada nesta fase.

## Protocols futuros

- `AdvCrmAiClient.classify(...)`
- `AdvCrmDecisionSink.submit_decision(...)`
