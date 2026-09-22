"""Partição treino/validação/teste sem vazamento de conversa ou paráfrase."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from experiments.attendance_signals_setfit.dataset import AnnotatedExample

SplitName = Literal["train", "validation", "test"]
SPLITS: tuple[SplitName, ...] = ("train", "validation", "test")


@dataclass(frozen=True, slots=True)
class SplitAssignment:
    example_id: str
    conversation_id: str
    paraphrase_group: str
    split: SplitName


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    train: tuple[AnnotatedExample, ...]
    validation: tuple[AnnotatedExample, ...]
    test: tuple[AnnotatedExample, ...]
    assignments: tuple[SplitAssignment, ...]

    def get(self, name: SplitName) -> tuple[AnnotatedExample, ...]:
        return getattr(self, name)


def _stable_bucket(key: str, seed: int, modulus: int = 100) -> int:
    digest = hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
    return int(digest[:8], 16) % modulus


def assert_no_leakage(assignments: Iterable[SplitAssignment]) -> None:
    """Garante que conversation_id e paraphrase_group não cruzam partições."""
    conv_to_split: dict[str, SplitName] = {}
    group_to_split: dict[str, SplitName] = {}
    for item in assignments:
        prev_c = conv_to_split.get(item.conversation_id)
        if prev_c is not None and prev_c != item.split:
            raise AssertionError(
                f"vazamento conversation_id={item.conversation_id!r}: {prev_c} vs {item.split}"
            )
        conv_to_split[item.conversation_id] = item.split

        prev_g = group_to_split.get(item.paraphrase_group)
        if prev_g is not None and prev_g != item.split:
            raise AssertionError(
                f"vazamento paraphrase_group={item.paraphrase_group!r}: {prev_g} vs {item.split}"
            )
        group_to_split[item.paraphrase_group] = item.split


def split_examples(
    examples: list[AnnotatedExample],
    *,
    seed: int = 42,
    train_ratio: float = 0.6,
    validation_ratio: float = 0.2,
) -> DatasetSplit:
    """Separa por *união* de conversation_id e paraphrase_group.

    Todos os exemplos que compartilham conversa **ou** grupo de paráfrase
    vão para a mesma partição. O balde é determinado pelo menor
    ``(conversation_id, paraphrase_group)`` lexicográfico do componente
    conexo — hash estável com ``seed``.

    Ambíguos permanecem no dataset (rótulo null); o filtro de treino binário
    ocorre depois, por sinal.
    """
    if not 0 < train_ratio < 1 or not 0 < validation_ratio < 1:
        raise ValueError("razões de split inválidas")
    if train_ratio + validation_ratio >= 1:
        raise ValueError("train_ratio + validation_ratio deve ser < 1")

    # Componentes conexos: aresta se mesma conversa ou mesmo paraphrase_group.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for ex in examples:
        eid = ex.example_id
        parent.setdefault(eid, eid)

    by_conv: dict[str, list[str]] = defaultdict(list)
    by_group: dict[str, list[str]] = defaultdict(list)
    for ex in examples:
        by_conv[ex.conversation_id].append(ex.example_id)
        by_group[ex.paraphrase_group].append(ex.example_id)

    for ids in by_conv.values():
        for other in ids[1:]:
            union(ids[0], other)
    for ids in by_group.values():
        for other in ids[1:]:
            union(ids[0], other)

    components: dict[str, list[AnnotatedExample]] = defaultdict(list)
    for ex in examples:
        components[find(ex.example_id)].append(ex)

    train_cut = int(train_ratio * 100)
    val_cut = int((train_ratio + validation_ratio) * 100)

    buckets: dict[SplitName, list[AnnotatedExample]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    assignments: list[SplitAssignment] = []

    for _root, members in sorted(components.items(), key=lambda item: item[0]):
        # Chave estável do componente: menor conversation_id + paraphrase_group.
        key_parts = sorted({(m.conversation_id, m.paraphrase_group) for m in members})
        component_key = "|".join(f"{c}:{g}" for c, g in key_parts)
        bucket = _stable_bucket(component_key, seed)
        if bucket < train_cut:
            split: SplitName = "train"
        elif bucket < val_cut:
            split = "validation"
        else:
            split = "test"
        for member in members:
            buckets[split].append(member)
            assignments.append(
                SplitAssignment(
                    example_id=member.example_id,
                    conversation_id=member.conversation_id,
                    paraphrase_group=member.paraphrase_group,
                    split=split,
                )
            )

    assert_no_leakage(assignments)

    return DatasetSplit(
        train=tuple(buckets["train"]),
        validation=tuple(buckets["validation"]),
        test=tuple(buckets["test"]),
        assignments=tuple(assignments),
    )


def split_counts(split: DatasetSplit) -> dict[str, int]:
    return {
        "train": len(split.train),
        "validation": len(split.validation),
        "test": len(split.test),
    }
