"""Contrato da âncora — tipos e vocabulário (spec 5.1).

Define AnchorCtx, GateCheck, GateResult e re-exporta as constantes de
sentinela de `core`. O contrato C1–C7 é battle-tested; estes tipos são o
vocabulário que o portão (gate.py) usa para impô-lo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Re-export do vocabulário de sentinela (conveniência para consumidores).
from core.sentinels import (  # noqa: F401
    ALL_SENTINEL_TOKENS,
    BLOCK_CLOSERS,
    BLOCK_OPENERS,
    BLOCK_PAIR,
    BLOCK_SENTINELS,
    NOTEREF_CLOSE,
    NOTEREF_OPEN,
)

CONTRACT_VERSION = "1.0.0"


class GateRejected(Exception):
    """Levantada quando uma âncora viola o contrato e aborta o degrau."""

    def __init__(self, result: "GateResult") -> None:
        self.result = result
        first = result.first_failure()
        super().__init__(first.message if first else "gate rejected")


@dataclass(frozen=True)
class AnchorCtx:
    """Tudo que os checks do portão precisam (spec 5.1)."""

    raw_hash: str
    anchor_filling_hash: str | None
    engine_version: str
    contract_version: str
    source_format: str
    source_edition: str
    translation_layer: str | None       # presente, pode ser None
    min_size: int
    size_unit: str                       # "words" | "chars"
    residual_noise_patterns: tuple[str, ...] = ()
    # Política de front matter (fase canônica). Opt-in: off por padrão para
    # preservar comportamento/golden; o runner liga para formatos estruturados.
    trim_front_matter: bool = False
    front_matter_min_run: int | None = None
    front_matter_zone: int | None = None

    def provenance_dict(self) -> dict:
        return {
            "source_format": self.source_format,
            "source_edition": self.source_edition,
            "translation_layer": self.translation_layer,
        }


@dataclass(frozen=True)
class GateCheck:
    code: str
    passed: bool
    message: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    contract_version: str
    body_hash: str | None
    body_size: int
    checks: tuple[GateCheck, ...]
    diagnostics: dict = field(default_factory=dict)

    def first_failure(self) -> GateCheck | None:
        for c in self.checks:
            if not c.passed:
                return c
        return None
