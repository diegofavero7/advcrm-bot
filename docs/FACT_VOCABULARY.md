# Vocabulário canônico de fatos

Chaves abertas no contrato `lead_understanding.v1` / `known_facts`. Este documento
lista os nomes **estáveis** consumidos por política, promoção e avaliação.
Não fecha o schema em enum e não acumula aliases.

Fonte de verdade no código: `app/domain/fact_vocabulary.py`.

O vocabulário é **catálogo de possibilidades**, não checklist. A existência da chave não
autoriza emiti-la: fato sem sustentação literal na mensagem citada é omitido.

| Chave | Valores | Significado | Consumidores |
| --- | --- | --- | --- |
| `explicit_human_request` | booleano canônico | Pedido explícito de atendente humano (não “advogado” genérico) | `policy:explicit_human_or_existing_client` |
| `relationship_to_detainee` | texto concreto | Relação com pessoa presa | avaliação / playbooks |
| `detainee_imprisoned` | booleano canônico | Pessoa referida está presa | `safety.promotion:prison_allowance` |
| `legal_request_subject` | subject id do catálogo | Assunto jurídico pedido; **não** usar `legal_request` | promoção taxonômica |
| `unauthorized_loan` | texto concreto | Empréstimo/contratação não autorizada | `promotion:banking_fraud` |
| `prison_circumstances` | texto concreto | Circunstância da prisão (ex.: flagrante) | `promotion:flagrant_arrest` |
| `violence_type` | texto concreto | Tipo/contexto de violência | `promotion:domestic_violence` |
| `minor_child_dependent` | texto concreto | Criança/menor envolvida | `promotion:child_custody` |

## Domínio booleano canônico

`case_facts.value` é `str` no contrato v1 (preservado). Para `explicit_human_request` e
`detainee_imprisoned` o domínio é **fechado e documentado**:

| Verdadeiro | Falso |
| --- | --- |
| `true`, `1`, `yes`, `sim` | `false`, `0`, `no`, `nao`, `não` |

- Qualquer outro literal é **inválido**: erro `canonical_boolean_fact_value`
  (coerência semântica, fail-closed). Não é renomeado, removido nem convertido para
  produzir sucesso.
- Truthiness genérica é proibida: `"false"` nunca significa verdadeiro.
- `parse_canonical_boolean` retorna `None` fora do domínio; `canonical_boolean_is_true`
  é o único consumo operacional.

### `explicit_human_request` — o que preenche e o que não preenche

| Situação | Valor |
| --- | --- |
| Lead pede atendente/pessoa real/humano | `true` |
| Lead nega explicitamente o pedido de transferência | `false` |
| Procura genérica por advogado/escritório | chave omitida |
| Pedido de andamento de processo | chave omitida (é `existing_client_case_status`) |
| Fala citada de terceiros | chave omitida |
| Instrução maliciosa / prompt injection | chave omitida |
| Nenhuma menção | chave omitida |

Consumo pela política (`detect_explicit_human_request`):

- `known_facts` do CRM com `from_trusted_crm_context=true` e valor canônico verdadeiro; **ou**
- `case_facts` com `certainty=explicit`, `source_message_ids` não vazios e todos
  apontando para declarações textuais do lead.

Em ambos os casos a chave precisa estar **resolvida** (`fact_state.v2`): se a mesma chave
aparecer com valores divergentes, o estado é NÃO resolvido e não autoriza handoff.

`non_legal_contact` continua resolvido por `non_legal_or_spam` — não foi reescrito para a
regra de pedido humano. Cliente existente obtém handoff por
`existing_client_case_status`, sem fabricar pedido explícito.

## Regras

- Placeholders técnicos rejeitados em `case_facts.value` (código
  `case_fact_placeholder_value`): marcadores de pipeline (`unknown_value`,
  `inferred_from_context`, `n/a`, `na`, `lead_untrusted_data`) e marcadores de ausência
  (`unknown`, `undefined`, `unspecified`, `not_specified`, `not_informed`,
  `desconhecido`, `desconhecida`, `nao_informado`, `não informado`, `null`, `none`).
- A proibição é restrita a `case_facts.value`: `unknown` permanece válido nos enums do
  contrato (`documents_availability`, `participants.role`,
  `procedural_situation.stage`).
- Desconhecido se omite; ausência de menção não é `false`.
- `certainty=explicit` exige sustentação na mensagem citada.
- ID de mensagem válido ≠ fidelidade semântica.
- Chave resolvida vs NÃO resolvida: `fact_state.v2` (ver `docs/contracts.md`). Valores
  divergentes na mesma chave não são resolvidos por recência, `role` nem
  `reply_to_message_id`; o contrato não representa correção.
- `from_trusted_crm_context=true` exige suporte em `known_facts` do request (mesma chave
  e valor equivalente). Sem suporte → `unverified_trusted_crm_context` (fail-closed).
  Mensagem do lead não concede confiança CRM.
- Política **não** faz matching textual em `reasoning_summary` nem frases livres.
