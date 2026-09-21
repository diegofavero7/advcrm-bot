# Contratos

Todos os modelos usam `extra="forbid"`. Campos anuláveis são `T | null` e aparecem em `required` no JSON Schema.

## Saídas da IA (Fase 1.1)

Somente:

- `lead_understanding.v1` — nome no runtime: `advcrm_lead_understanding_v1`
- `triage_next_step.v1` — nome: `advcrm_triage_next_step_v1`

`triage_analysis_request.v1` é envelope interno, **não** grammar do modelo.

Formato enviado:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "advcrm_lead_understanding_v1",
      "strict": true,
      "schema": {}
    }
  }
}
```

O `schema` vem do modelo Pydantic; o hash deve coincidir com `artifacts/schemas/*.json`.

## TriageProposal (interno)

- `lead_understanding` / `triage_next_step` anuláveis
- `safe_fallback` com `source=deterministic_policy` em falha técnica
- Sem fabricar next step; sem `requires_human_handoff` automático em falha técnica

## finish_reason

- `stop` — ok
- `length` — truncamento, fail-closed (nunca validar parcial)
- `refusal` — falha controlada
- outro — erro de protocolo

## Protocols

- `AdvCrmAiClient.complete_structured(...) -> StructuredCompletionResult`
- `AdvCrmDecisionSink` — stub futuro
