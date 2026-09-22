# Lacunas de regras de política

Documento da integração de `decide_conservative_action` (`conservative_action.v7`).
**Não** introduz matching por palavra-chave no texto do lead.

## Precedência obrigatória (primeira correspondente vence)

| # | Identificador estável | Gatilho |
|---|---|---|
| 1 | `existing_client_case_status` | `intent=existing_client_case_status` |
| 2 | `explicit_human_or_existing_client` | pedido humano canônico válido **ou** `intent=existing_client_other_request` |
| 3 | `flagrant_arrest_requires_handoff` | `subject=flagrant_arrest` (estruturado) |
| 4 | `imminent_deadline_requires_handoff` | `imminent_deadline` operacional (só extrator) |
| 5 | `immediate_urgency_blocks_auto_route` | `urgency=immediate` **ou** `safety.recommend_handoff=true` |
| 6 | `domestic_violence_urgency_requires_handoff` | `domestic_violence` operacional **e** `urgency in {high, immediate}` |
| 7 | `non_legal_or_spam` | `intent in {spam, non_legal_contact}` |
| 8 | `low_confidence` | banda de confiança baixa |
| 9 | `max_questions` | limite de perguntas automáticas atingido |
| 10 | `undetermined_classification_requires_clarification` | `primary_area=undetermined` **e** `subject=undetermined` |
| 11 | `spam_legal_inconsistency_guard` | pós-decisão (`spam_inconsistency_guard.v1`) |

Recomendações (não substituem o modelo): `needs_clarification`,
`high_confidence_route_candidate`.

## Distinção

| Conceito | Significado |
|---|---|
| **Regra ausente** | Mesmo com sinais corretos no entendimento validado, não existe ramo que produza a ação esperada. |
| **Sinal omitido** | A política *teria* efeito se o extrator trouxesse o sinal operacional. |
| **Candidato** | `model_detected` / `taxonomy_derived` / `extractor_detected` — auditoria. |
| **Operacional (`effective`)** | Só riscos confirmados pelo extrator; alimenta política e métricas efetivas. |

Validade de catálogo ≠ fidelidade semântica: subject incorreto não pode fabricar risco operacional só via taxonomia.

---

## Resolvidas

### `crim_flagrant` → `flagrant_arrest_requires_handoff`

- Gatilho: `subject == flagrant_arrest` (não depende do extrator).

### `urgency_deadline` → `imminent_deadline_requires_handoff` (v5)

- Gatilho: sinais **operacionais** contêm `imminent_deadline` (extrator).
- Subject previdenciário incorreto + taxonomia `arrest_or_detention` **não** entra no operacional
  se o extrator só viu prazo.
- Sem matching textual na política.

### Classificação totalmente indeterminada

- Chave canônica `legal_issue_description`.
- Posição 10 na precedência: só age depois de segurança, pedido humano, cliente
  existente, spam/não jurídico, baixa confiança e limite de perguntas.
- Predicado: `primary_area=undetermined` **e** `subject=undetermined`. Independe de
  `confidence` e de `ambiguity.needs_confirmation` — o modelo marcar
  `needs_confirmation=false` não remove a proteção (defeito observado em
  `correction_later`).
- Ambiguidade **parcial** (área ou assunto definidos) permanece recomendativa.
- A reconciliação final recusa `route_lead` totalmente indeterminado reutilizando
  `build_undetermined_clarification_step()` — mesma proveniência determinística.

### `family_violence` → `domestic_violence_urgency_requires_handoff` (v7)

- Predicado exato: `RiskFlag.DOMESTIC_VIOLENCE` nos sinais **operacionais**
  (`effective`) **e** `understanding.urgency in {high, immediate}`.
- **Não** exige `safety.level=high` nem `safety.recommend_handoff=true`: era exatamente
  essa exigência que fazia `immediate_urgency_blocks_auto_route` não acionar com
  `urgency=high` + `recommend_handoff=false`, roteando o caso automaticamente.
- **Não** generaliza: risco qualquer não basta, `urgency=high` isolada não basta, prisão
  categórica isolada não basta, e menção sem sinal operacional estruturado não basta.
- Posição 6: depois de prazo iminente e urgência imediata, preservando as regras que
  protegem situações mais graves (`priority=critical`); esta usa `priority=high` e
  `handoff_reason=sensitive_situation`, sem inventar gravidade adicional.
- Representa encaminhamento operacional, não diagnóstico de perigo imediato.

---

## Ainda válido

- Promoção taxonômica exige fato `certainty=explicit` + `source_message_ids` válidos
  (`app/safety/promotion.py`). Subject no catálogo sozinho não basta.
- `prison_date` inferred **não** promove `arrest_or_detention`.
- `imminent_deadline` operacional exige extrator (sem regra taxonômica).
- Política em `conservative_action.v7`; composição `effective_safety.v3` (agregação
  affirmed a partir de v1 ou v2; negadas/hipotéticas não operacionais).
- Prompt do extrator: `safety_signals.v4` com contrato `safety_signals.v2` (padrão);
  `v3` permanece no disco sem carregamento padrão;
  legado `safety_signals.v2` prompt + contrato `v1` via setting.
- `prison_allowance` legítimo: extrator ou `detainee_imprisoned` explícito → operacional; sem handoff só por isso.
- Artefatos antigos sem `effective_safety_signals` / sem `candidate` → métricas efetivas `not_observed`.
- **Correção documental:** no fluxo normal, `effective` **não** é “só pelo extrator” —
  também promove taxonomia + fato explícito. No caminho degradado, sim: somente extrator.

---

## Caminho degradado — `degraded_safety.v2`

Quando o understanding é rejeitado por falha de **conteúdo de saída**
(`schema_validation` | `structured_validation` | `semantic_validation`), o pipeline
executa o extrator **uma vez** só com o request. Não reutiliza payload rejeitado.

**Não** liberam safety: `context_limit`, `contract_version`, transporte, auth, protocolo, etc.

Entrada tipada: `ExtractorOnlySafety` (payload `safety_signals.v2` + composição sem
model/taxonomy). Composição inconsistente → `failed_closed`, não handoff.

Precedência: (1) perigo imediato → (2) prazo iminente → (3) DV + ajuda urgente → (4) revisão.

### Tabela de gatilhos

| Gatilho | Condição estruturada | Ação | Motivo | Prioridade |
|---|---|---|---|---|
| B | `immediate_danger=present` + ocorrência afirmada relacionada em {violence, DV, self_harm, medical} | `human_handoff` | `immediate_risk` | critical |
| C | `imminent_deadline` affirmed + temporal ∈ {near_future, ongoing} | `human_handoff` | `legal_deadline_risk` | critical |
| A | `urgent_help_request=present` relacionado a ocorrência `domestic_violence` affirmed | `human_handoff` | `sensitive_situation` | high |
| D | demais (incl. DV+violence sem cue; histórico isolado; negado/hipotético; v1 sem cues) | revisão interna | — | — |

Proibidos: `route_lead`, `ignore`, `ask_question`, next-step, promoção de payload rejeitado.

### `family_violence`

Com understanding **inválido** e extrator v2 validando DV afirmada **+**
`urgent_help_request` relacionado: rota de atendimento comprovada por **fixture**
(`degraded_dv_urgent_help_handoff` / testes unitários). No live, não presumir que o
understanding falhará — se válido, vale a política plena (`conservative_action.v7`).

Correspondência literal de `evidence_quote` ≠ prova de interpretação semântica correta.
