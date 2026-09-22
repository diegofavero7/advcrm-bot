# spam_inconsistency_guard.v1

Regra versionada aplicada **depois** de `decide_conservative_action`, antes de
aceitar `action=ignore` por spam.

## Gatilho

1. Entendimento validado com `intent=spam`.
2. Decisão candidata seria `ignore` (`non_legal_or_spam`).
3. Há **classificação jurídica específica** estrutural:
   - `primary_area` ∉ {other, undetermined}
   - `subject` ∉ {other, undetermined}

## Efeito

- Substitui `ignore` por `request_human_review`
- `requires_human_handoff=true`, `handoff_reason=conflicting_information`
- `policy_flags=["spam_legal_inconsistency_guard"]`
- Proveniência: `policy_version=spam_inconsistency_guard.v1`
- **Não** reclassifica intent/área/assunto para lead jurídico
- **Não** é handoff de atendimento por mérito; é revisão interna por inconsistência
  de classificação (distinto também de `safe_fallback` por erro técnico)

## Não dispara

- Spam coerente: area/subject indeterminados ou other
- Spam que só “menciona” tema jurídico em fatos/texto, mas permanece other/undetermined
- Qualquer intent ≠ spam

## Pacote

Integrada no resolvedor `conservative_action.v7` (`app/policies/resolution.py`).
