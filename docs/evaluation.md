# Avaliação (Fase 1.1)

## Offline vs live

| Modo | Quando | Rede |
|---|---|---|
| Offline | CI / `make eval-offline` | Não |
| Live | Manual autorizado | Sim, runtime real |

## Métricas

JSON/schema válidos, acurácia intent/área/assunto, recall handoff,
**recall de riscos do modelo** vs **recall/completude efetivos** (métricas distintas),
fatos inventados, perguntas repetidas, fail-closed, latências, retries.

- `model_risk_label_recall` (informativo; recall do understanding).
- `effective_risk_label_recall` / completude (metas) = **ponta a ponta**
  (casos com `required_risks` que falham antes da composição entram no denominador).
- `*_observed` = só casos em que a composição foi observada.
- `forbidden_risks` / `risks_exhaustive` para FP anotados; `required_risks=[]` **não**
  proíbe riscos. Extras sem anotação → `unevaluated_extra_risks`.
- Aliases `risk_label_recall` / `risk_case_complete` estão **deprecated** nas metas
  (não aparecem como FAIL duplicado).
- Sem casos aplicáveis → meta = `not_evaluated` (não `PASS`/`FAIL` enganoso).
- +1 inferência (prompt alinhado ao contrato ativo; padrão `safety_signals.v4` /
  contrato `v2`) por evento live bem-sucedido até a política, **ou** no caminho
  `degraded_safety` após rejeição estrutural/semântica do understanding.
- `pipeline_status=degraded_safety` ≠ sucesso de understanding: intent/área/assunto
  permanecem não observados ou falhos; riscos efetivos do extrator podem ser avaliados;
  handoff de atendimento só conta se `triage_next_step` for handoff (não `safe_fallback`).
- Ver `METRICS_COVERAGE.md` no artefato e [`FACT_VOCABULARY.md`](FACT_VOCABULARY.md).

## Caminho da decisão (`eval_expected.v5`)

Além da ação, o expected pode exigir o **caminho**:

| Campo | Significado |
|---|---|
| `acceptable_actions` | ação efetiva aceitável |
| `acceptable_policy_rules` | regra (ou flag) exigida quando ela é parte do comportamento requerido |
| `path_expectations` | ramos por `pipeline_status`; regras/cues/motivo **não** herdam o topo |
| `acceptable_handoff_reasons` / `acceptable_priorities` | opcionais; ausência → n/a |
| `extractor_cues` / `risk_temporal` / `required_extractor_risks` | checagens do extrator v2; ausência de anotação ≠ PASS |
| `require_model_inference_skipped` | dispensa obrigatória da 2ª inferência |
| `handoff_required` | `true`/`false`; `null` = ausência de expectativa |
| `required_fact_keys` / `forbidden_fact_keys` | presença/ausência de chave |
| `required_fact_values` / `forbidden_fact_values` | correção do conteúdo do fato **resolvido** |
| `critical_expectations` | checagens deste caso que bloqueiam a aprovação |
| `annotation_rationale` | justificativa independente da saída atual |

**v4 → v5:** cues/temporal/extractor_risks compartilhados herdam o topo; lista vazia
de associações é explícita; preflight de critical; não entregue bloqueia live;
temporal distingue ocorrência ausente vs valor errado; artefato guarda
`expected` + `expected_active`; dual-fail preserva detalhes/payload do safety.

**v3 → v4 (negócio):** `family_violence` e regressões de safety passam a declarar
expectativas condicionadas. No caminho `degraded_safety`, DV + pedido urgente exige
`degraded_safety_domestic_violence_urgent_help_requires_handoff` com
`sensitive_situation`/`high` — sem aceitar `immediate_danger` só porque o live
exagerou. Reanálise de artefatos antigos gera novo artefato com
`expectations_version` atual; snapshots em `artifacts/` não são
sobrescritos automaticamente.

- Handoff vindo de outra regra **não** satisfaz `acceptable_policy_rules`: `explicit_human`
  precisa acionar `explicit_human_or_existing_client`, não `non_legal_or_spam`.
- Anotar regra só onde ela é o requisito; não transformar todo caso em teste de detalhe
  interno incidental.
- Presença de chave e correção de valor são métricas **distintas**
  (`required_facts_complete_rate` vs `fact_value_correct_rate`).
- `resolved_fact_values` aplica `fact_state.v2` — mesma resolução da política.
- Chave anotada e **NÃO resolvida** (valores divergentes) é **falha**, registrada em
  `unresolved_annotated_facts`. Encontrar o valor esperado entre valores conflitantes
  não comprova que o modelo entregou o estado correto e não conta como acerto. Valor
  proibido conta como violação mesmo com a chave não resolvida — ele foi emitido.

### Invariante derivada

`undetermined_route_lead_cases` não depende de anotação: área e assunto `undetermined`
com `route_lead` é violação em qualquer caso.

### Cobertura: zero falhas ≠ sucesso

Contagem de falhas só é informativa com cobertura. Offline não executa inferência nem
aplica política, então nenhum caso é avaliável para as metas bloqueantes: o relatório
mostra `not_evaluated` e o número de avaliáveis, em vez de “0 falhas”.

- `critical_evaluable_cases`: casos anotados com ao menos uma checagem crítica fora de
  `not_observed`/`not_applicable`.
- `undetermined_route_evaluable_cases`: casos com classificação **e** ação efetiva
  observadas.

Com zero avaliáveis, `critical_expectations` e `undetermined_route_lead` retornam
`not_evaluated` — nunca `PASS` — e não entram em `blocking_failures`.

## Metas

Metas live iniciais: JSON/schema 100%, handoff 100%, inventados/repetidas 0%,
intent/área ≥90%, assunto ≥80%.

Metas próprias adicionadas em `eval_expected.v3` (mantidas em v4):

| Meta | Limiar |
|---|---|
| `required_facts_complete_rate` | 100% |
| `fact_value_correct_rate` | 100% |
| `action_match_rate` | 100% |
| `policy_rule_match_rate` | 100% |
| `model_inference_skipped_match_rate` | 100% |
| `critical_failed_cases_max` | 0 (bloqueante) |
| `undetermined_route_lead_cases_max` | 0 (bloqueante) |

`BLOCKING_TARGET_KEYS` (`critical_expectations`, `undetermined_route_lead`) vetam a
aprovação operacional mesmo que todas as demais taxas estejam acima do limiar:
`write_artifacts` força `passed_targets=false` e registra `blocking_failures`.
Limiares não são reduzidos para o live passar.

## Denominadores

Três grandezas distintas, nunca intercambiáveis:

| Campo | Cálculo |
|---|---|
| `risk_required_occurrences` | soma de `len(required_risks)` por caso |
| `risk_distinct_categories` | tamanho da **união** dos rótulos requeridos |
| `risk_applicable_cases` | casos com `required_risks` não vazio |

Mais `risk_categories_observed` / `risk_categories_not_observed`.

O relatório anterior renderizava “Rótulos de risco esperados/observados/não observados:
6/6/0” ao lado de “req=8”: a primeira linha media **categorias distintas** e a segunda,
**ocorrências**. O rótulo genérico “esperados” sugeria a mesma grandeza. A linha agora
nomeia cada denominador explicitamente.

`null` continua ausência de expectativa; `0/0` é ausência de cobertura, não evidência de
ausência de erro.

## Interpretação

- Confiança do modelo não é probabilidade calibrada nem jurídica.
- Avaliação sintética não substitui revisão humana.
- Relatórios live em `artifacts/evaluations/` — não versionar conteúdo sensível.
- Testes com mocks comprovam regra e integração determinística; **não** comprovam
  compreensão do modelo. Extração corrigida em produção só se demonstra em live.
- Snapshots antigos são preservados; versões históricas desconhecidas não são
  preenchidas retroativamente.
