# Máquina de estados

O **estado oficial** pertence ao AdvCRM. Este módulo valida transições propostas e rejeita as inválidas sem correção silenciosa.

## Estados

- `collecting_messages`
- `pending_classification`
- `classified`
- `collecting_information`
- `waiting_lead_reply` — pergunta selecionada; aguarda resposta do lead
- `pending_action_approval` — decisão aguarda autorização do AdvCRM
- `pending_human_review`
- `ready_for_handoff`
- `human_assigned`
- `completed` (terminal)
- `cancelled` (terminal)
- `failed` — recuperação controlada apenas para `pending_classification` ou `pending_human_review`

## Regras importantes

1. Não existe `human_assigned → collecting_information` (automação não retoma silenciosamente após handoff).
2. `human_assigned` só segue para `completed` ou `cancelled`.
3. Transições inválidas levantam `InvalidTransitionError`.

```mermaid
stateDiagram-v2
  collecting_messages --> pending_classification
  collecting_messages --> pending_human_review
  collecting_messages --> cancelled
  collecting_messages --> failed
  pending_classification --> classified
  pending_classification --> collecting_messages
  pending_classification --> pending_human_review
  pending_classification --> failed
  classified --> collecting_information
  classified --> pending_action_approval
  classified --> pending_human_review
  classified --> cancelled
  collecting_information --> waiting_lead_reply
  collecting_information --> pending_action_approval
  collecting_information --> classified
  collecting_information --> pending_human_review
  collecting_information --> cancelled
  collecting_information --> failed
  waiting_lead_reply --> collecting_information
  waiting_lead_reply --> pending_classification
  waiting_lead_reply --> pending_human_review
  waiting_lead_reply --> cancelled
  waiting_lead_reply --> failed
  pending_action_approval --> waiting_lead_reply
  pending_action_approval --> ready_for_handoff
  pending_action_approval --> pending_human_review
  pending_action_approval --> cancelled
  pending_action_approval --> failed
  pending_human_review --> human_assigned
  pending_human_review --> collecting_information
  pending_human_review --> ready_for_handoff
  pending_human_review --> cancelled
  pending_human_review --> failed
  ready_for_handoff --> human_assigned
  ready_for_handoff --> cancelled
  ready_for_handoff --> failed
  human_assigned --> completed
  human_assigned --> cancelled
  failed --> pending_classification
  failed --> pending_human_review
```
