"""Carregamento e validação do dataset anotado do experimento."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from experiments.attendance_signals_setfit.labels import LabelValue, validate_labels


@dataclass(frozen=True, slots=True)
class MessageTurn:
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class AnnotatedExample:
    example_id: str
    conversation_id: str
    paraphrase_group: str
    messages: tuple[MessageTurn, ...]
    labels: dict[str, LabelValue]
    justification: str
    difficulty_groups: tuple[str, ...] = ()
    ambiguous_signals: tuple[str, ...] = ()
    needs_human_review: bool = False
    notes: str = ""

    @property
    def text(self) -> str:
        """Texto concatenado para o classificador (conversa completa)."""
        parts: list[str] = []
        for turn in self.messages:
            parts.append(f"{turn.role}: {turn.text.strip()}")
        return "\n".join(parts)


@dataclass
class AnnotatedDataset:
    dataset_id: str
    examples: list[AnnotatedExample] = field(default_factory=list)

    def by_id(self) -> dict[str, AnnotatedExample]:
        return {ex.example_id: ex for ex in self.examples}


def _parse_example(raw: dict[str, Any]) -> AnnotatedExample:
    required = (
        "example_id",
        "conversation_id",
        "paraphrase_group",
        "messages",
        "labels",
        "justification",
    )
    for key in required:
        if key not in raw:
            raise ValueError(f"exemplo sem campo obrigatório '{key}'")

    messages_raw = raw["messages"]
    if not isinstance(messages_raw, list) or not messages_raw:
        raise ValueError(f"{raw['example_id']}: messages deve ser lista não vazia")

    messages: list[MessageTurn] = []
    for msg in messages_raw:
        if not isinstance(msg, dict) or "role" not in msg or "text" not in msg:
            raise ValueError(f"{raw['example_id']}: mensagem inválida")
        text = str(msg["text"]).strip()
        if not text:
            raise ValueError(f"{raw['example_id']}: mensagem vazia")
        messages.append(MessageTurn(role=str(msg["role"]), text=text))

    labels = validate_labels(raw["labels"])
    ambiguous = tuple(raw.get("ambiguous_signals") or ())
    for signal in ambiguous:
        if signal not in labels:
            raise ValueError(f"{raw['example_id']}: ambiguous_signals desconhecido: {signal}")
        if labels[signal] is not None:
            raise ValueError(
                f"{raw['example_id']}: sinal ambíguo '{signal}' deve ter rótulo null, "
                "não falso/verdadeiro automático"
            )

    return AnnotatedExample(
        example_id=str(raw["example_id"]),
        conversation_id=str(raw["conversation_id"]),
        paraphrase_group=str(raw["paraphrase_group"]),
        messages=tuple(messages),
        labels=labels,
        justification=str(raw["justification"]).strip(),
        difficulty_groups=tuple(raw.get("difficulty_groups") or ()),
        ambiguous_signals=ambiguous,
        needs_human_review=bool(raw.get("needs_human_review", False)),
        notes=str(raw.get("notes") or ""),
    )


def load_jsonl(path: Path) -> AnnotatedDataset:
    if not path.is_file():
        raise FileNotFoundError(f"dataset ausente: {path}")

    examples: list[AnnotatedExample] = []
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: JSON inválido") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"{path}:{line_no}: esperado objeto")
            example = _parse_example(raw)
            if example.example_id in seen_ids:
                raise ValueError(f"example_id duplicado: {example.example_id}")
            seen_ids.add(example.example_id)
            examples.append(example)

    meta_path = path.with_name("dataset_meta.json")
    dataset_id = "unknown"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        dataset_id = str(meta.get("dataset_id", "unknown"))

    return AnnotatedDataset(dataset_id=dataset_id, examples=examples)


def distribution_report(dataset: AnnotatedDataset) -> dict[str, Any]:
    """Contagens por sinal (true/false/null) — útil para revisão humana."""
    from experiments.attendance_signals_setfit import SIGNAL_IDS

    report: dict[str, Any] = {
        "dataset_id": dataset.dataset_id,
        "n_examples": len(dataset.examples),
        "n_conversations": len({e.conversation_id for e in dataset.examples}),
        "n_paraphrase_groups": len({e.paraphrase_group for e in dataset.examples}),
        "needs_human_review": sum(1 for e in dataset.examples if e.needs_human_review),
        "signals": {},
    }
    for signal in SIGNAL_IDS:
        counts = {"true": 0, "false": 0, "null": 0}
        for ex in dataset.examples:
            value = ex.labels[signal]
            if value is True:
                counts["true"] += 1
            elif value is False:
                counts["false"] += 1
            else:
                counts["null"] += 1
        report["signals"][signal] = counts
    return report
