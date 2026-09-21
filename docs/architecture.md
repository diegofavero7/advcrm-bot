# Arquitetura — AdvCRM Bot

## Fase 1.1

```mermaid
flowchart TD
  Request["TriageAnalysisRequest"] --> Context["ContextBuilder"]
  Context --> UPrompt["Prompt understanding"]
  UPrompt --> AI["AiRuntimeClient"]
  AI --> UVal["Pydantic + invariantes"]
  UVal --> Playbook["resolve_playbook"]
  Playbook --> NPrompt["Prompt next step"]
  NPrompt --> AI
  AI --> NVal["Pydantic + políticas"]
  NVal --> Proposal["TriageProposal"]
```

- Cliente: [`app/clients/ai_runtime.py`](../app/clients/ai_runtime.py)
- Serviços: [`app/application/services.py`](../app/application/services.py)
- Proposta: [`app/schemas/proposal.py`](../app/schemas/proposal.py)
- Prompts: [`app/prompts/`](../app/prompts/)

Falha técnica → `safe_fallback` determinístico (revisão interna), sem `TriageNextStep` artificial.

## Autoridade

AdvCRM executa. Bot propõe. Sem WhatsApp/CRM write nesta fase.
