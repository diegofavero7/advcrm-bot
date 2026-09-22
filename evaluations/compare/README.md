# Comparação controlada — understanding only

Conjunto **reservado** (`understanding_compare_reserved.v1`) para comparar
variantes da etapa de understanding. Não acrescentar estes casos aos prompts.

Após uso em ajustes de prompt, o conjunto deixa de ser benchmark independente.

## O que o runtime permite

O bot **não** envia `model` no payload de `generate/structured`. O servidor AdvCRM AI
escolhe o checkpoint carregado. `AI_RUNTIME_MODEL` no bot é só rótulo/registro.

Para baseline vs candidate reais: duas execuções com `base_url` distintos (ou o
mesmo endpoint após trocar o modelo no runtime) e depois `diff` dos artefatos.

## Offline (sintético — sem rede)

```bash
.venv/bin/python scripts/generate_compare_cases.py

.venv/bin/python -m evaluations.compare run --variant baseline \
  --synthetic evaluations/compare/synthetic/baseline_responses.json

.venv/bin/python -m evaluations.compare run --variant candidate \
  --synthetic evaluations/compare/synthetic/candidate_responses.json

.venv/bin/python -m evaluations.compare diff \
  --baseline artifacts/evaluations/<baseline_dir> \
  --candidate artifacts/evaluations/<candidate_dir>

# ou:
make eval-compare-offline
```

## Live (próxima etapa — explícito)

```bash
.venv/bin/python -m evaluations.compare run --variant baseline --live \
  --variant-config path/to/baseline.json
```

`baseline.json` mínimo:

```json
{"base_url": "http://localhost:8001", "requested_model": "rótulo-opcional"}
```

Sem `--live`, nenhuma conexão é aberta. Sem `base_url` no live → erro claro
(sem fallback silencioso para o default).

## Escopo

Inclui: request → contexto → inferência → validações (estrutural/semântica/CRM) → score.

Não inclui: extrator de safety, política, next-step, handoff efetivo, riscos efetivos.
