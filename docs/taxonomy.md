# Taxonomia

Versão: `legal_subjects.v1`  
Arquivo: `app/taxonomy/data/legal_subjects.v1.yaml`

Assuntos oficiais **nunca** são texto livre. Todo assunto pertence ao catálogo da área.

## Nomes desambiguados

| Evitar | Usar |
|---|---|
| `disability_benefit` | `permanent_disability_benefit` |
| `unfair_dismissal` | `dismissal_without_just_cause` |
| `alimony` | `child_support` / `spousal_support` |

## Subassuntos detalhados (Fase 1)

Apenas:

- `social_security/prison_allowance`
- `consumer/vehicle_purchase_irregularities`

Demais assuntos: sem catálogo fino (lista vazia). Se informados, apenas `other` / `undetermined`.

## Áreas prioritárias

`social_security`, `consumer`, `labor`, `family`, `civil`, `criminal`

## Áreas não prioritárias

`tax`, `business`, `real_estate`, `administrative`, `traffic`, `succession`, `other`, `undetermined` — classificação + encaminhamento humano via playbook `generic_non_priority`.
