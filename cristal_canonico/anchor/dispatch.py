"""A escada de dispatch (spec 5.3).

"Usa se existe, senão cria." Cria é escada, portão em cada degrau. Cria
NUNCA é "sintetiza código custom e confia".

    porta de tamanho (fail-fast no bruto)
    Degrau 1 — USA      (catalog.lookup)
    Degrau 2 — CANÔNICO (preclean + cp; preenchimento trivial)
    Degrau 3 — CONFIG   (Engine A; vocabulário compartilhado — DIFERIDA)
    Degrau 4 — HOOK     (humano, tipado — NÃO auto-sintetiza)

Cada degrau passa pelo portão; o que resolve escreve no catálogo, congelado
(vira "existe" no Degrau 1). Esperar 100% de auto-criação é a expectativa
errada — Degrau 4 escala para humano.
"""

from __future__ import annotations

from core.text import count_body_size, sha256_text
from pipeline.preclean import preclean
from pipeline.segment import segment

from .catalog import AnchorCatalog
from .contract import AnchorCtx, GateResult
from .gate import validate_body_static, validate_roundtrip


class TooSmallError(Exception):
    """Bruto abaixo do min_size — nem entra na escada (porta de tamanho)."""

    def __init__(self, size: int, min_size: int, unit: str) -> None:
        self.size, self.min_size, self.unit = size, min_size, unit
        super().__init__(f"entrada inválida: {size} < {min_size} {unit}")


class NeedsTypedHook(Exception):
    """Degrau 4 — nenhuma rota automática resolveu; escala para humano."""

    def __init__(self, raw_hash: str, diagnostics: dict) -> None:
        self.raw_hash = raw_hash
        self.diagnostics = diagnostics
        super().__init__(f"texto {raw_hash[:12]} exige hook tipado (humano)")


def gate_passes(
    body: str, structure: dict, ctx: AnchorCtx
) -> tuple[bool, GateResult, GateResult]:
    """Portão completo: estático (inclui C7) + round-trip (C1/C1')."""
    static = validate_body_static(body, ctx)
    roundtrip = validate_roundtrip(body, structure, ctx)
    return (static.passed and roundtrip.passed, static, roundtrip)


def run_engine(text_raw: str, toml: str) -> str:  # pragma: no cover - diferido
    """Engine A (config declarativa) — DIFERIDA (spec Seção 6).

    O vocabulário de regras emerge dos primeiros ports; não se projeta a
    priori. Enquanto não há ports provados, este degrau fica inerte.
    """
    raise NotImplementedError(
        "Engine A diferida: o vocabulário de config emerge dos ports (spec 6). "
        "Comece pelo protocolo B (um módulo por âncora com a mesma assinatura)."
    )


def _apply_entry(entry: dict, text_raw: str, ctx: AnchorCtx) -> str:
    """Re-aplica determinísticamente o preenchimento registrado (Degrau 1)."""
    rung = entry["rung"]
    if rung == "canonical":
        body, _ = preclean(text_raw, ctx)
        if entry["body_hash"] != sha256_text(body):
            raise RuntimeError(
                f"drift de engine: body_hash divergente para {entry['text_id'][:12]} "
                "(re-bake necessário — contrato/engine mudou)"
            )
        return body
    raise NotImplementedError(
        f"re-aplicação do degrau {rung!r} ainda não implementada "
        "(config/hook dependem da Engine A / módulo de hook)"
    )


def dispatch(
    text_raw: str,
    ctx: AnchorCtx,
    catalog: AnchorCatalog | None = None,
    config_candidates: list[str] | None = None,
) -> str:
    """Atravessa a escada e devolve o corpo canônico (ou aborta)."""
    if catalog is None:
        catalog = AnchorCatalog()

    # PORTA DE TAMANHO — fail-fast no bruto, antes da escada.
    raw_size = count_body_size(text_raw, ctx.size_unit)
    if raw_size < ctx.min_size:
        raise TooSmallError(raw_size, ctx.min_size, ctx.size_unit)

    raw_hash = sha256_text(text_raw)

    # Degrau 1 — USA
    entry = catalog.lookup(raw_hash)
    if entry is not None:
        return _apply_entry(entry, text_raw, ctx)

    # Degrau 2 — CANÔNICO (preenchimento trivial: preclean + cp)
    body, _ = preclean(text_raw, ctx)
    structure = segment(body, ctx)
    ok, static, roundtrip = gate_passes(body, structure, ctx)
    if ok:
        catalog.register(
            raw_hash,
            rung="canonical",
            filling_kind="none",
            filling_ref=None,
            ctx=ctx,
            body_size=static.body_size,
            body_hash=static.body_hash,
        )
        return body

    # Degrau 3 — CONFIG (Engine A, diferida): só se houver candidatos.
    for toml in config_candidates or []:
        body = run_engine(text_raw, toml)
        structure = segment(body, ctx)
        ok, static, roundtrip = gate_passes(body, structure, ctx)
        if ok:
            catalog.register(
                raw_hash,
                rung="config",
                filling_kind="toml",
                filling_ref=sha256_text(toml),
                ctx=ctx,
                body_size=static.body_size,
                body_hash=static.body_hash,
            )
            return body

    # Degrau 4 — HOOK (humano, tipado) — NÃO auto-sintetiza.
    raise NeedsTypedHook(
        raw_hash,
        {
            "static_failure": static.first_failure(),
            "roundtrip_failure": roundtrip.first_failure(),
        },
    )
