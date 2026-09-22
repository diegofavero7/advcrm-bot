"""Manifesto de execução da comparação de understanding.

Registra o necessário para reproduzir e verificar compatibilidade entre variantes.
Campos indisponíveis ficam como ``unknown`` — nunca inferidos do alias do modelo.
Não persiste tokens, credenciais nem cabeçalhos de autenticação.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

UNKNOWN = "unknown"

COMPARE_KIND = "understanding_compare.v1"


@dataclass(frozen=True, slots=True)
class VariantIdentity:
    """Identidade solicitada vs reportada — sem deduzir checkpoint do alias."""

    variant_id: str
    requested_model: str = UNKNOWN
    reported_models: tuple[str, ...] = ()
    runtime_base_url: str = UNKNOWN
    checkpoint: str = UNKNOWN
    quantization: str = UNKNOWN


@dataclass(frozen=True, slots=True)
class SharedEvalFingerprint:
    """Fingerprint compartilhado — deve coincidir entre variantes comparáveis."""

    prompt_version: str
    prompt_hash: str
    schema_version: str
    schema_hash: str
    taxonomy_version: str
    taxonomy_hash: str
    expectations_version: str
    case_ids: tuple[str, ...]
    case_hashes: dict[str, str]
    temperature: float | None
    max_tokens: int | None
    reserved_suite: str


@dataclass
class RunManifest:
    """Manifesto de uma execução de variante (understanding-only)."""

    kind: str = COMPARE_KIND
    run_id: str = ""
    variant_id: str = ""
    mode: str = "offline"  # offline | live
    created_at: str = ""
    code_revision: str = UNKNOWN
    code_dirty: bool | None = None
    identity: VariantIdentity | None = None
    shared: SharedEvalFingerprint | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_code_revision(repo_root: Path) -> tuple[str, bool | None]:
    """Retorna (revisão, dirty). Falha → (unknown, None) sem inventar."""
    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return UNKNOWN, None
    try:
        dirty_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        dirty = bool(dirty_out.strip())
    except (OSError, subprocess.CalledProcessError):
        return rev or UNKNOWN, None
    return rev or UNKNOWN, dirty


def build_shared_fingerprint(
    *,
    prompt_version: str,
    prompt_hash: str,
    schema_version: str,
    schema_path: Path,
    taxonomy_version: str,
    taxonomy_path: Path,
    expectations_version: str,
    case_ids: list[str],
    case_hashes: dict[str, str],
    temperature: float | None,
    max_tokens: int | None,
    reserved_suite: str,
) -> SharedEvalFingerprint:
    return SharedEvalFingerprint(
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        schema_version=schema_version,
        schema_hash=file_sha256(schema_path),
        taxonomy_version=taxonomy_version,
        taxonomy_hash=file_sha256(taxonomy_path),
        expectations_version=expectations_version,
        case_ids=tuple(case_ids),
        case_hashes=dict(case_hashes),
        temperature=temperature,
        max_tokens=max_tokens,
        reserved_suite=reserved_suite,
    )


def new_run_manifest(
    *,
    variant_id: str,
    mode: str,
    identity: VariantIdentity,
    shared: SharedEvalFingerprint,
    repo_root: Path,
    notes: list[str] | None = None,
) -> RunManifest:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rev, dirty = detect_code_revision(repo_root)
    return RunManifest(
        kind=COMPARE_KIND,
        run_id=f"{stamp}_{variant_id}",
        variant_id=variant_id,
        mode=mode,
        created_at=stamp,
        code_revision=rev,
        code_dirty=dirty,
        identity=identity,
        shared=shared,
        notes=list(notes or []),
    )


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest inválido: {path}")
    return data


COMPATIBILITY_KEYS: tuple[str, ...] = (
    "prompt_version",
    "prompt_hash",
    "schema_version",
    "schema_hash",
    "taxonomy_version",
    "taxonomy_hash",
    "expectations_version",
    "reserved_suite",
    "temperature",
    "max_tokens",
)


def compare_manifests_compatible(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Verifica se dois manifests descrevem o mesmo experimento controlado.

    Diferenças de casos, entradas, prompts, contratos ou expectations → incompatível.
    Não declara experimento controlado quando a configuração compartilhada diverge.
    """
    reasons: list[str] = []
    if baseline.get("kind") != COMPARE_KIND or candidate.get("kind") != COMPARE_KIND:
        reasons.append("kind deve ser understanding_compare.v1 em ambos")
    b_shared = baseline.get("shared") or {}
    c_shared = candidate.get("shared") or {}
    if not isinstance(b_shared, dict) or not isinstance(c_shared, dict):
        reasons.append("shared fingerprint ausente")
        return False, reasons

    for key in COMPATIBILITY_KEYS:
        if b_shared.get(key) != c_shared.get(key):
            reasons.append(f"shared.{key} diverge")

    b_ids = list(b_shared.get("case_ids") or [])
    c_ids = list(c_shared.get("case_ids") or [])
    if b_ids != c_ids:
        reasons.append("case_ids divergem (ordem ou conjunto)")

    b_hashes = b_shared.get("case_hashes") or {}
    c_hashes = c_shared.get("case_hashes") or {}
    if not isinstance(b_hashes, dict) or not isinstance(c_hashes, dict):
        reasons.append("case_hashes ausente ou inválido")
    elif b_hashes != c_hashes:
        reasons.append("case_hashes divergem (conteúdo de entrada)")

    return (len(reasons) == 0), reasons
