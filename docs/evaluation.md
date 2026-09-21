# Avaliação (Fase 1.1)

## Offline vs live

| Modo | Quando | Rede |
|---|---|---|
| Offline | CI / `make eval-offline` | Não |
| Live | Manual autorizado | Sim, runtime real |

## Métricas

JSON/schema válidos, acurácia intent/área/assunto, recall handoff/riscos, fatos inventados, perguntas repetidas, fail-closed, latências, retries, distribuição por área.

Metas live iniciais: JSON/schema 100%, handoff 100%, inventados/repetidas 0%, intent/área ≥90%, assunto ≥80%.

## Interpretação

- Confiança do modelo não é probabilidade calibrada nem jurídica.
- Avaliação sintética não substitui revisão humana.
- Relatórios live em `artifacts/evaluations/` — não versionar conteúdo sensível.
