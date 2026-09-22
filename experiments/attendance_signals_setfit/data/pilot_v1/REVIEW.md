# Revisão humana — `attendance_signals_pilot.v1`

Conjunto **sintético piloto**. Não treinar como se fosse gold definitivo.

## O que já está marcado para revisão (`needs_human_review=true`)

| ID | Motivo |
| --- | --- |
| `pil_005` | “meu processo” sem declaração explícita de cliente do escritório |
| `pil_012` | pedido humano em nome de terceiro |
| `pil_018` | “Atendente” isolado — ambíguo (`null`) |
| `pil_021` | “Tem novidade?” — andamento vs follow-up social |
| `pil_022` | sugestão vaga de vínculo |
| `pil_023` | advogado responsável vs atendente humano |
| `pil_025` | andamento referido a terceiro |

## Partes que exigem revisão antes do treino real

1. **Protocolo de terceiros** — confirmar se pedido em nome de familiar conta como sinal do lead ou permanece negativo.
2. **Fronteira advogado × atendente** — fechar regra para “quero falar com o advogado”.
3. **“Meu processo/caso”** — quando vira `existing_client_declaration` vs só `case_status_request`.
4. **Ambíguos (`null`)** — não promover a `false` sem consenso; se necessário, reescrever o texto.
5. **Cobertura de paráfrases** — cada `paraphrase_group` deve permanecer na mesma partição; revisar se há grupos demais no treino e poucos no teste.
6. **Proibições** — não importar `evaluations/cases`, `evaluations/compare` nem lives anteriores.

## O que este dataset **não** prova

- Identidade do lead ou vínculo CRM.
- Que abstenção do classificador autorize handoff.
- Qualidade de precisão em português jurídico (dados sintéticos + mocks).
