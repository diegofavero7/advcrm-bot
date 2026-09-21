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

Saída em `artifacts/evaluations/<timestamp>_live/` (não versionar resultados sensíveis).

## Metas iniciais (live)

| Métrica | Meta |
|---|---|
| JSON válido | 100% |
| Schema válido | 100% |
| Recall handoff | 100% |
| Fatos inventados | 0% |
| Perguntas repetidas | 0% |
| Intenção | ≥ 90% |
| Área | ≥ 90% |
| Assunto | ≥ 80% |
