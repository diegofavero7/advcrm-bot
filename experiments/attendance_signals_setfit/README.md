# Experimento: SetFit — sinais de atendimento

**Modo:** avaliação / infraestrutura local.  
**Não** substitui understanding, **não** altera política, **não** autoriza handoff.

> O experimento está preparado; a qualidade só será conhecida após treinamento e avaliação em dados separados. Dados sintéticos e mocks **não** autorizam declarar melhoria de precisão.

## Sinais (podem coexistir)

| ID | Origem | Significado |
| --- | --- | --- |
| `explicit_human_request` | vocabulário canônico | Pedido explícito de atendente/humano (≠ “preciso de advogado”) |
| `existing_client_declaration` | **novo** (interno) | Lead declara ser cliente / ter vínculo com o escritório |
| `case_status_request` | alinhado a `HandoffReason` | Pedido de andamento/atualização de caso/processo |

Classificação descreve **conteúdo** da conversa. Não concede confiança CRM nem prova identidade.

Não confundir:

- “preciso de advogado” ≠ pedido de transferência humana;
- “quero falar com atendente” ≠ declaração de cliente existente;
- existência de processo ≠ vínculo confirmado ao escritório.

### Intent do Qwen

Não comparar estes sinais diretamente com o enum de `intent` sem o mapeamento em `labels.INTENT_MAPPING_NOTES`. Limitações: intent mistura declaração + tipo de pedido; pedido humano vive em `case_facts`, não em intent.

## Estratégia de modelo

**Três classificadores SetFit binários independentes** (não um multilabel acoplado).

Justificativa (docs SetFit multilabel/`one-vs-rest`): multilabel é suportado, mas aqui cada sinal tem base rate, dificuldade e banda de abstenção próprias, escolhidas **só na validação**. Binários independentes permitem retreino isolado e limiares distintos sem um limiar universal.

Body proposto:

- **Checkpoint:** [`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
- **Licença:** Apache-2.0 (model card)
- **Por quê:** multilíngue compacto (384-d), inclui `pt` / `pt-br`, amplamente usado como body SetFit; adequado a CPU.
- **Não promete** precisão em português jurídico.

Device padrão: **CPU** (não disputar GPU com Qwen).

## Dependências

Separadas do bot obrigatório:

```bash
pip install -e ".[setfit-experiment]"
```

Testes e `make check` **não** exigem SetFit, download nem rede.

## Dados

- `data/pilot_v1/` — sintético piloto revisável (`REVIEW.md`).
- Split por `conversation_id` **e** `paraphrase_group` (componentes conexos).
- Rótulo `null` = ambíguo; **não** vira negativo.
- Proibido treinar/calibrar com `evaluations/cases`, compare reserved ou lives.

## Confiança e abstenção

- Scores por sinal são salvos; **não** são probabilidade calibrada.
- Decisões: `positive` | `negative` | `inconclusive`.
- Limiares por sinal na **validação** apenas.
- Abstenção **não** vira decisão operacional.

## Comandos

```bash
# 1) Preparar / revisar partições (sem rede)
.venv/bin/python -m experiments.attendance_signals_setfit prepare-data

# 2) Baixar checkpoint (futuro — exige flag explícita de rede)
.venv/bin/python -m experiments.attendance_signals_setfit download-checkpoint \
  --i-understand-network \
  --out artifacts/experiments/attendance_signals_setfit/checkpoints/paraphrase-multilingual-MiniLM-L12-v2

# 3) Treinar localmente (stub por padrão; real com --allow-train + checkpoint local)
.venv/bin/python -m experiments.attendance_signals_setfit train
.venv/bin/python -m experiments.attendance_signals_setfit train --allow-train --device cpu

# 4) Avaliar (stub/fixture por padrão)
.venv/bin/python -m experiments.attendance_signals_setfit evaluate --split test

# 5) Carregar artefato local (erro claro se ausente)
.venv/bin/python -m experiments.attendance_signals_setfit load-artifact \
  --artifact artifacts/experiments/attendance_signals_setfit/latest
```

Makefile: `make experiment-signals-prepare`, `experiment-signals-train-stub`, `experiment-signals-eval-stub`.

## Testes

```bash
.venv/bin/python -m pytest tests/experiments -q --cov= --no-cov
```

## Fontes oficiais

- SetFit: https://huggingface.co/docs/setfit/en/index
- Multilabel: https://huggingface.co/docs/setfit/en/how_to/multilabel
- Checkpoint: https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

## Próximos passos

1. Revisão humana dos IDs em `REVIEW.md` e protocolo de terceiros/ambiguidades.
2. Download do checkpoint em máquina dedicada (CPU).
3. Treino local com `--allow-train`.
4. Avaliação no split de teste (limpo); reportar cobertura + métricas globais com abstenção.
5. Só então discutir eventual calibração — ainda **fora** do caminho de handoff.
