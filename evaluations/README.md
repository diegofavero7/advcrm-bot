# Avaliação AdvCRM Bot

Suíte de casos sintéticos/anonimizados para medir classificação e próximo passo.

## Princípios

- Sem dados reais sem anonimização.
- Confiança do modelo **não** é probabilidade jurídica.
- Avaliação sintética **não** substitui validação humana.
- Live **nunca** é obrigatório no CI.

## Estrutura

- `cases/` — requests de entrada
- `expected/` — intenções/áreas/assuntos aceitáveis, handoff, riscos, fatos
- `runner.py` — offline (estrutura) ou `--live`
- `metrics.py` — métricas e relatório

## Offline (CI)

```bash
.venv/bin/python scripts/generate_eval_cases.py
.venv/bin/python -m evaluations.runner
# ou: make eval-offline
```

## Live (manual)

Requer `AI_RUNTIME_ENABLED=true`, modelo e runtime configurados.

```bash
.venv/bin/python -m evaluations.runner --live
```

Saída em `artifacts/evaluations/<timestamp>_live/` (gitignored):

- `report.md` / `results.json`
- `cases/<case_id>.json` — proposta completa, expected, erro sanitizado, versões
- `METRICS_COVERAGE.md` — o que cada métrica realmente mede

Reanálise de artefato legado (sem nova chamada live):

```bash
.venv/bin/python -m evaluations.reanalyze \
  --source artifacts/evaluations/20260921T150823Z_live
```

## Comparação de understanding (reservada)

Infraestrutura para comparar variantes **somente** na etapa de understanding
(offline sintético agora; live na próxima etapa). Ver `evaluations/compare/README.md`.

```bash
make eval-compare-offline
```

## Metas iniciais (live)

| Métrica | Meta |
|---|---|
| JSON parse (observado) | 100% |
| Contrato estrutural (observado) | 100% |
| Recall handoff (atendimento) | 100% |
| Recall riscos por rótulo | 100% |
| Completude de riscos por caso | 100% |
| Fatos proibidos (hit) | 0% |
| Perguntas repetidas (quando observada) | 0% |
| Intenção e2e | ≥ 90% |
| Área e2e | ≥ 90% |
| Assunto e2e | ≥ 80% |
| Fatos obrigatórios (presença) | 100% |
| Fatos resolvidos (valor correto) | 100% |
| Ação efetiva | 100% |
| Regra de política (onde anotada) | 100% |
| Inferência pulada (onde anotada) | 100% |
| Expectativas críticas falhas | 0 (bloqueante) |
| `route_lead` totalmente indeterminado | 0 (bloqueante) |

Denominadores: expectativa vazia = `not_applicable`; sem saída = `not_observed`.
Sem casos aplicáveis → meta = `not_evaluated` (não `FAIL`).
`0/0` é ausência de cobertura, não evidência de ausência de erro.

Metas bloqueantes reprovam o conjunto mesmo com as demais em `PASS`:
`results.json` traz `blocking_failures` e `passed_targets=false`.
Sem caso avaliável (offline, sem inferência nem política) elas retornam
`not_evaluated` e o relatório mostra `avaliáveis=0` — “0 falhas” não é sucesso.

### Denominadores de risco (três grandezas)

| Campo | Cálculo |
|---|---|
| `risk_required_occurrences` | soma de `len(required_risks)` |
| `risk_distinct_categories` | tamanho da união dos rótulos |
| `risk_applicable_cases` | casos com `required_risks` não vazio |

O relatório antigo rotulava categorias distintas como “rótulos esperados” ao lado de
`req=` (ocorrências), o que fazia 6 e 8 parecerem a mesma contagem.

### Expectativas do caminho da decisão (`eval_expected.v4`)

`acceptable_policy_rules`, `require_model_inference_skipped`, `required_fact_values`,
`forbidden_fact_values`, `critical_expectations`, `annotation_rationale`, e (v4)
`path_expectations`, `acceptable_handoff_reasons`, `acceptable_priorities`,
`extractor_cues`, `risk_temporal`, `required_extractor_risks`.
Handoff vindo de outra regra **não** satisfaz a regra anotada.
Com `path_expectations`, regras do ramo ativo **não** herdam o topo.
Valores de fato usam o estado **resolvido** (`fact_state.v2`): valores divergentes na
mesma chave ficam NÃO resolvidos e contam como falha, não como acerto parcial.

### Riscos: modelo vs efetivos

- `model_risk_label_recall` = rótulos em `lead_understanding.safety.detected_risks`
  / total requeridos (honestidade do understanding).
- `effective_risk_label_recall` = rótulos nos sinais efetivos / total requeridos.
- `effective_risk_case_complete_rate` = casos com **todos** os riscos efetivos /
  aplicáveis.
- Artefato antigo sem `effective_safety_signals` → efetivo `not_observed`
  (reanálise não fabrica taxonomia/extrator).
- Risco categórico ≠ urgência/handoff automático, exceto pelas regras estruturadas de
  `conservative_action.v7` (ver `docs/POLICY_GAPS.md`).

Relatório separa e2e vs condicionado a saída válida, e latência todos vs sucessos.
