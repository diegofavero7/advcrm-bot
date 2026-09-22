# Contratos

Todos os modelos usam `extra="forbid"`. Campos anuláveis são `T | null` e aparecem em `required` no JSON Schema.

## Transporte AdvCRM AI (Fase 1.1.1)

Endpoint: `POST {AI_RUNTIME_BASE_URL}{AI_RUNTIME_STRUCTURED_GENERATION_PATH}`  
Padrão do path: `/internal/v1/generate/structured`

Envelope enviado (sem `model`, `response_format`, `strict` ou `schema_name`):

```json
{
  "messages": [
    {"role": "system", "content": "<prompt versionado>"},
    {"role": "user", "content": "<contexto serializado>"}
  ],
  "max_tokens": 2048,
  "temperature": 0,
  "schema": {}
}
```

`schema` é o JSON Schema estrito gerado pelo bot (objeto, não string).  
`schema_name` permanece apenas como identificador interno de logs/métricas.

Resposta HTTP 200:

```json
{
  "data": {},
  "model": "<modelo retornado pelo runtime>",
  "finish_reason": "stop",
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

Mapeamento para `StructuredCompletionResult`:

| Campo API | Campo resultado |
|---|---|
| `data` | `parsed_content` |
| `model` | `model` |
| `finish_reason` | `finish_reason` |
| `usage` | `usage` (três inteiros obrigatórios) |
| (medido) | `latency_ms`, `retry_count` |
| header `X-Request-Id` (se presente) | `request_id` opcional |

Limites validados antes do HTTP (fail-closed, sem truncar): mensagens 1..32; content 1..32000; agregado ≤24000; schema ≤16 KiB; max_tokens 1..2048; temperature 0..1.

Erros HTTP relevantes (AdvCRM AI):

- 422 — body inválido do chamador; sem retry; não logar body.
- 503 — inferência indisponível; retry conforme política transitória, **salvo** quando
  `retryable` é exatamente o booleano `false`.
- 502 genérico (`{"detail":"Resposta de inferência inválida."}` sem `code`) — tratado como
  falha de servidor/upstream; **pode** retry (comportamento documentado para causas
  desconhecidas / envelope sem campos aditivos), **salvo** `retryable: false` (booleano).
- 502 com `code=structured_validation_failed` — revalidação fail-closed do JSON contra o
  schema do chamador; **não** retry; categoria `structured_validation`. Diagnóstico
  sanitizado opcional: `validator`, `instance_path`, `schema_path`, `missing_properties`,
  `request_id`.
- `retryable: false` (booleano exato) em status normalmente retentáveis — **não** retry,
  mas mantém a categoria HTTP apropriada (`server` / `rate_limit`); **não** reclassifica
  como schema. Ausente, inválido ou string `"false"` **não** equivalem a booleano false.
- Decisão de retry **nunca** usa substring de `detail`.

## Saídas da IA

Somente:

- `lead_understanding.v1` — nome interno: `advcrm_lead_understanding_v1`
- `triage_next_step.v1` — nome interno: `advcrm_triage_next_step_v1`

`triage_analysis_request.v1` é envelope interno, **não** grammar do modelo.

O hash do schema deve coincidir com `artifacts/schemas/*.json`.

### Desconhecimento em `lead_understanding.v1`

- `documents_availability` admite `unknown` quando o lead não informou posse de documentos.
- `procedural_situation.existing_case`, `prior_request` e `prior_denial` são `bool | null`:
  use `null` para desconhecimento. **Não** use `false` como “não informado”.
- Ausência de menção na conversa ≠ evidência negativa. Inventar `existing_case=false`
  ou documentos disponíveis sem suporte viola a fidelidade dos fatos.
- O contrato v1 já cobre desconhecimento via `null`/enum `unknown`; não há necessidade
  de bump de schema só por esse motivo. Qualquer mudança semântica futura (ex.: enum
  tri-estado explícito) deve ser versionada (`lead_understanding.v2`), nunca silenciosa.

### `case_facts`: esparso, válido e resolvido

`case_facts` é lista de fatos observados, **não** formulário. O vocabulário canônico é
catálogo de possibilidades; a existência da chave não autoriza emiti-la.

- Desconhecido se **omite**. Ausência de menção não é `false`; `false` só representa
  negação sustentada.
- Marcadores técnicos não são valores de fato. `case_facts.value` rejeita
  `inferred_from_context`, `unknown_value`, `n/a`, `lead_untrusted_data` e marcadores de
  ausência (`unknown`, `desconhecido`, `not_specified`, `não informado`, `null`, …) com o
  código `case_fact_placeholder_value`. A proibição é **restrita a `case_facts.value`**:
  `unknown` permanece legítimo em `documents_availability`, `participants.role` e
  `procedural_situation.stage`.
- Chaves booleanas canônicas validam domínio fechado (ver adiante). Valor inválido é
  rejeitado fail-closed, nunca corrigido em silêncio.

#### Estado resolvido vs NÃO resolvido (`fact_state.v2`)

O contrato v1 transporta `case_facts` como lista plana. Os campos por fato são `key`,
`value`, `certainty`, `source_message_ids` e `from_trusted_crm_context`. **Nenhum** deles
representa correção, nem identifica o alvo factual (pessoa, período, evento).

`ConversationMessage.reply_to_message_id` é ponteiro de **contexto de resposta**. Uma
resposta pode corrigir, complementar, mudar de assunto ou falar de outra pessoa — o
contrato não distingue. Encadeamento, recência, `role` igual e chave igual, isolados ou
combinados, **não** são evidência de correção.

> `fact_state.v1` tratava “mesma chave + ancestralidade + mesmo role” como correção
> explícita. Isso era inferência disfarçada de regra determinística e foi **removido**.

Regra de resolução, por chave, sobre os valores **distintos** presentes:

- exatamente um valor distinto → chave **resolvida** com esse valor;
- dois ou mais → chave **NÃO resolvida**. Todos os fatos são preservados e nenhum é
  eleito. Consumidores de valor único tratam a chave como sem autorização, nunca como
  “o valor mais recente”.

Duplicatas não geram conflito: valores iguais após normalização são o mesmo valor. Em
chaves booleanas canônicas a comparação usa o booleano parseado (`true` ≡ `sim`;
`true` vs `false` é conflito). Não há sinonímia livre nem matching textual das mensagens.

**Bloqueio de contrato.** Substituição determinística exigiria que o contrato
representasse a correção **e** o alvo factual — por exemplo, referência ao fato corrigido
mais um identificador de alvo. Isso é mudança de contrato externo
(`lead_understanding.v1`) e está fora desta revisão. Até lá não existe caminho de
supersessão, e o prompt (`lead_understanding.v9`) instrui o modelo a emitir **um** fato
por alvo, já corrigido.

Mesma resolução é consumida pela política (`case_fact_state`, `crm_fact_state`), pela
promoção taxonômica (`find_supporting_fact`) e pela avaliação (`resolved_fact_values`),
para não divergirem.

### Verificação determinística vs semântica

Verificável no schema/pipeline (fail-closed):

- tipos, enums, taxonomia área/assunto/subassunto;
- `case_facts.value` não pode ser placeholder conhecido (`inferred_from_context`, etc.);
- domínio de valor das chaves booleanas canônicas (`canonical_boolean_fact_value`);
- `source_message_ids` presentes ou `from_trusted_crm_context=true`;
- `from_trusted_crm_context=true` exige suporte em `request.known_facts` (mesma chave e
  valor equivalente sob a normalização de `fact_state.v2`); sem suporte →
  `unverified_trusted_crm_context` (fail-closed semântico). Mensagem do lead e a mera
  existência da chave com outro valor **não** concedem confiança CRM. O flag não é
  rebaixado nem o fato apagado para passar;
- IDs de `known_facts` no request devem existir nas mensagens.

Exige julgamento semântico / avaliação humana ou live (não coberto por mock):

- se o texto da mensagem citada realmente sustenta o fato;
- se subassuntos/riscos/documentos foram inventados;
- ausência de alucinação em geral.

A validação de referência comprova que a mensagem **existe**, não que ela sustenta o
fato. Não apresentar isso como confirmação independente de fidelidade.

Testes mockados/offline **não** comprovam fidelidade factual do modelo.

### Múltiplas demandas e taxonomia

- A demanda principal sustentada vai em `primary_area`/`subject`; a segunda em
  `secondary_area` (área distinta) e/ou `ambiguity`.
- O `subject` de uma demanda **nunca** vira `subsubject` de outra. `subsubjects`
  pertencem exclusivamente ao `subject` selecionado.
- `subsubjects=[]` é resposta válida e esperada quando nada estiver sustentado.
- Subassunto fora do catálogo é rejeitado por `subsubject_not_in_catalog`; subassunto em
  `subject` sem catálogo detalhado, por `subsubject_without_catalog`. Fail-closed: o item
  inválido não é apagado nem o catálogo ampliado para acomodar a saída.
- Sem categoria exata, use `other`/`undetermined` da área — não aumentar especificidade
  para “encaixar”. “Demitido” não estabelece “sem justa causa”; sigla de órgão isolada
  (ex.: INSS) não é documento mencionado.

### Pergunta única e classificação indeterminada

O validador rejeita `proposed_question` com mais de um caractere `?` (mecânico).
Coerência `ask_question` (lacunas/seleção/pergunta/handoff) está no Pydantic + prompt
`triage_next_step.v3`. JSON Schema **não** usa `if`/`then` (GBNF llama.cpp unsupported).

Classificação **totalmente** indeterminada (`primary_area=undetermined` **e**
`subject=undetermined`) é resolvida pela política obrigatória
`undetermined_classification_requires_clarification` (`conservative_action.v7`), com
chave canônica `legal_issue_description`, **antes** da inferência de next_step.
O predicado **não** depende de `confidence` nem de `ambiguity.needs_confirmation`: sem
área e sem assunto não existe destino para rotear, mesmo que o modelo afirme não precisar
confirmar. Distinto de `needs_clarification` recomendativa (ambiguidade parcial, em que
área **ou** assunto está definido). A reconciliação final aplica a mesma resolução
determinística quando o modelo propõe `route_lead` nessa condição — mesma proveniência,
sem segundo mecanismo.

Pedido explícito de atendente humano: chave canônica `explicit_human_request` em
`known_facts` (CRM trusted) **ou** `case_facts` (certainty=explicit e
`source_message_ids` apontando só para declarações textuais do lead). Sem matching
textual, sem resumo do modelo.

A chave precisa estar **resolvida** na sua fonte: valores divergentes (`true` e `false`)
deixam o estado NÃO resolvido e **não** autorizam handoff — fato conflitante não é
autorização inequívoca, nem pelo mais recente nem pelo “mais forte”. Conflito dentro do
CRM também não autoriza e não é resolvido por fato do lead. A mesma guarda vale na
promoção taxonômica: `detainee_imprisoned` conflitante não promove
`arrest_or_detention`.

`explicit_human_request` e `detainee_imprisoned` têm **domínio booleano fechado** no
`case_facts.value` (string no contrato v1): `true|1|yes|sim` e `false|0|no|nao|não`.
Qualquer outro literal é **inválido** — rejeitado com o código estável
`canonical_boolean_fact_value`, nunca renomeado, removido nem convertido. Truthiness
genérica é proibida: `"false"` é falso e texto livre não é verdadeiro. Procurar advogado,
pedir andamento, negar o pedido, citar fala de terceiros e instrução maliciosa **não**
preenchem a chave. Ver `app/domain/fact_vocabulary.py`.

### Riscos: candidatos vs operacionais

- `lead_understanding.safety.detected_risks` = **somente** o retorno do modelo (`model_detected`).
- Operacionais (`effective`): extrator confirmou (riscos **afirmados** agregados de v1/v2)
  **ou** taxonomia + fato explícito estruturado (`certainty=explicit`, IDs válidos).
  No caminho **degradado**, só o extrator entra na composição.
- Subject no catálogo sozinho **não** promove. `certainty=inferred` **não** promove.
- `imminent_deadline` operacional exige extrator (sem promoção taxonômica).
- A política `conservative_action.v7` consome sinais operacionais. Flagrante continua por
  `subject=flagrant_arrest`. Composição: `effective_safety.v3`.
- `domestic_violence` **operacional** + `urgency in {high, immediate}` exige handoff
  (`domestic_violence_urgency_requires_handoff`) no caminho **normal** (understanding válido).
- Contrato `safety_signals.v1` preservado; `safety_signals.v2` adiciona asserção,
  temporalidade, `urgent_help_request` e `immediate_danger` (evidência própria).
  Prompt: `safety_signals.v4` (contrato v2) / `safety_signals.v2` (contrato v1).
  (`safety_signals.v3` permanece no repositório, não carregado por padrão.)
  `evidence_quote` literal ≠ prova semântica.

## Autenticação S2S (confirmada na documentação AdvCRM AI)

```text
Authorization: Bearer <ADVCRM_AI_INTERNAL_TOKEN>
X-AdvCRM-Organization-Id: <uuid>   # obrigatório em /internal/v1/*
X-AdvCRM-User-Id: <uuid>           # opcional; auditoria
```

Pendências / não inventar:

- Mapeamento `tenant_id` (bot) ↔ `organization_id` (AdvCRM AI) **não** está estabelecido; use `AI_RUNTIME_ORGANIZATION_ID` ou parâmetro confiável.
- OpenAPI não define envelope de `refusal` nem erros de inferência no estilo llama.cpp; respostas inesperadas são tratadas de forma sanitizada (sem logar body 422).

## TriageProposal (interno)

- `status`: `success` | `failed_closed` | `degraded_safety`
  - `degraded_safety` = understanding rejeitado + safety válido; **não** implica
    understanding válido nem sucesso semântico completo
- `lead_understanding` / `triage_next_step` anuláveis
- `triage_next_step` = decisão **efetiva** (política plena, política degradada ou modelo reconciliado)
- `model_next_step_proposal` = proposta bruta do LLM quando a inferência de next_step ocorreu
- `safety_signals` / `effective_safety_signals` = extrator + união com proveniência
  (não muta `lead_understanding.safety.detected_risks`; no caminho degradado só extrator)
- `decision_provenance` = origem (`deterministic_policy` | `model`), `policy_version`
  (`conservative_action.v7` ou `degraded_safety.v2`), `policy_rule_id`, `policy_status`,
  `model_inference_skipped`, `supporting_signal_refs` (opcional)
- `error` permanece preenchido em `degraded_safety` (diagnóstico original do understanding)
- `safe_fallback` com `source=deterministic_policy` em falha técnica/política **ou**
  revisão interna do caminho degradado (revisão interna ≠ handoff de atendimento)
- Sem fabricar next step a partir de payload rejeitado; sem alterar contratos v1 da IA

Precedência de política: ver docstring em `app/policies/resolution.py` e lacunas em
[`POLICY_GAPS.md`](POLICY_GAPS.md).

## finish_reason

- `stop` — ok
- `length` — truncamento, fail-closed (nunca validar parcial)
- `refusal` — falha controlada (se aparecer; não documentado no OpenAPI)
- outro — erro de protocolo

## Readiness

- AdvCRM AI `/health` = liveness (não depende de llama.cpp; **não** usar como prova de modelo)
- AdvCRM AI `/ready` = readiness (token S2S no processo + health de inferência só se `INFERENCE_ENABLED`)
- Bot: runtime só entra no `/ready` local quando `AI_RUNTIME_REQUIRED=true` via `AI_RUNTIME_READY_PATH`

## Protocols

- `AdvCrmAiClient.complete_structured(...) -> StructuredCompletionResult`
- `AdvCrmDecisionSink` — stub futuro
