"""Identificadores de sinal e semântica de rótulos do experimento.

Compatibilidade com o vocabulário operacional:
- ``explicit_human_request`` — mesmo significado que ``app.domain.fact_vocabulary``.
- ``case_status_request`` — alinhado a ``HandoffReason.CASE_STATUS_REQUEST`` /
  intent ``existing_client_case_status`` *como descrição de conteúdo*, não como
  prova de vínculo cadastral.
- ``existing_client_declaration`` — **novo** identificador interno: o lead declara
  ser cliente / ter processo no escritório. Não existe chave canônica equivalente
  em ``known_facts``/``case_facts``; não concede ``from_trusted_crm_context``.

Valores de rótulo:
- ``true`` / ``false`` — positivo / negativo anotado.
- ``null`` — ambíguo ou sem cobertura; **não** vira negativo no treino/métricas
  binárias (exemplo excluído daquele sinal).
"""

from __future__ import annotations

from typing import Any, Literal

from experiments.attendance_signals_setfit import SIGNAL_IDS

LabelValue = bool | None
Decision = Literal["positive", "negative", "inconclusive"]

# Mapeamento documentado vs intent do understanding (NÃO comparar métricas
# diretamente sem esta conversão e suas limitações).
INTENT_MAPPING_NOTES = {
    "explicit_human_request": (
        "Não há intent dedicado. No understanding o sinal vive em case_facts "
        "(chave explicit_human_request). Intent pode permanecer new_legal_lead "
        "ou undetermined. Comparar com intent do Qwen sem este mapeamento é inválido."
    ),
    "existing_client_declaration": (
        "Parcialmente relacionado a intents existing_client_*; o intent mistura "
        "declaração de vínculo + tipo de pedido. Este sinal isola só a declaração "
        "de conteúdo. Não prova identidade nem CRM."
    ),
    "case_status_request": (
        "Relacionado a intent=existing_client_case_status, mas o intent exige "
        "interpretação de cliente existente. Aqui só o pedido de andamento/atualização "
        "no texto. Existência de processo ≠ vínculo confirmado ao escritório."
    ),
}


def normalize_label(raw: Any) -> LabelValue:
    """Converte anotação JSON em True / False / None (ambíguo)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"true", "1", "yes", "sim", "positive", "pos"}:
            return True
        if lowered in {"false", "0", "no", "nao", "não", "negative", "neg"}:
            return False
        if lowered in {"null", "none", "ambiguous", "ambigous", "inconclusive", "?"}:
            return None
    raise ValueError(f"rótulo inválido: {raw!r}")


def validate_labels(labels: dict[str, Any]) -> dict[str, LabelValue]:
    missing = [s for s in SIGNAL_IDS if s not in labels]
    if missing:
        raise ValueError(f"rótulos ausentes: {missing}")
    extra = [k for k in labels if k not in SIGNAL_IDS]
    if extra:
        raise ValueError(f"rótulos desconhecidos: {extra}")
    return {signal: normalize_label(labels[signal]) for signal in SIGNAL_IDS}


def is_train_eligible(label: LabelValue) -> bool:
    """Ambíguos (None) não entram como negativos nem positivos no treino binário."""
    return label is not None
