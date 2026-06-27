"""O portão — a imposição única do contrato (spec 5.2).

Função única que toda âncora atravessa, em duas partes:

* validate_body_static — C2 (conservação de sentinela), C4 (isolamento de
  corpo), C3/C5 parcial (hashable + body_hash), C6 (proveniência), C7
  (tamanho mínimo no corpo).
* validate_roundtrip   — C1 (eixo round-trip) e C1' (cobertura sem
  overlap/gap).

Semântica: PURO, determinístico, sem efeito colateral além do veredito. O
portão NUNCA conserta. Falha ⇒ aborta (o dispatcher trata GateResult
passed=False como fim-de-degrau). GateResult carrega contract_version e o
freeze_key nos diagnostics.

C3 e C5 (estabilidade ENTRE execuções) são provados pelo teste diferencial/
golden — o portão só computa o body_hash; a estabilidade dele exige comparar
dois bakes.
"""

from __future__ import annotations

import re

from core.sentinels import (
    BLOCK_CLOSERS,
    BLOCK_OPENERS,
    BLOCK_PAIR,
    BLOCK_SENTINELS,
    NOTEREF_CLOSE,
    NOTEREF_OPEN,
)
from core.text import count_body_size, freeze_key, sha256_text, strip_sentinels

from .contract import AnchorCtx, GateCheck, GateResult


# --- helpers ----------------------------------------------------------------

def _line_offsets(body: str):
    """Itera (line, char_start, char_end) coerente com segment/split('\\n')."""
    pos = 0
    for line in body.split("\n"):
        start = pos
        end = start + len(line)
        pos = end + 1
        yield line, start, end


def _sentinel_char_mask(body: str) -> list[bool]:
    """True nas posições de char que pertencem a uma LINHA de sentinela de bloco."""
    mask = [False] * len(body)
    for line, start, end in _line_offsets(body):
        if line.strip() in BLOCK_SENTINELS:
            for i in range(start, min(end, len(body))):
                mask[i] = True
    return mask


def _diagnostics(ctx: AnchorCtx, body_hash: str | None) -> dict:
    return {
        "contract_version": ctx.contract_version,
        "body_hash": body_hash,
        "freeze_key": freeze_key(
            ctx.raw_hash, ctx.anchor_filling_hash, ctx.engine_version
        ),
    }


# --- C2 ---------------------------------------------------------------------

def _check_sentinels_isolated(body: str) -> GateCheck:
    offenders = []
    for line, start, _ in _line_offsets(body):
        if any(tok in line for tok in BLOCK_SENTINELS) and line.strip() not in BLOCK_SENTINELS:
            offenders.append({"char_start": start, "line": line})
    return GateCheck(
        code="sentinels_isolated",
        passed=not offenders,
        message="sentinelas de bloco isolados em linha própria"
        if not offenders
        else f"{len(offenders)} linha(s) com sentinela de bloco não-isolado",
        details={"offenders": offenders},
    )


def _check_sentinels_balanced(body: str) -> GateCheck:
    stack: list[str] = []
    error = None
    for line, start, _ in _line_offsets(body):
        s = line.strip()
        if s in BLOCK_OPENERS:
            stack.append(s)
        elif s in BLOCK_CLOSERS:
            if not stack:
                error = {"reason": "close sem open", "char_start": start, "token": s}
                break
            opener = stack.pop()
            if BLOCK_PAIR.get(opener) != s:
                error = {
                    "reason": "close não casa com open",
                    "char_start": start,
                    "open": opener,
                    "close": s,
                }
                break
    if error is None and stack:
        error = {"reason": "open sem close", "open": stack[-1]}
    return GateCheck(
        code="sentinels_balanced",
        passed=error is None,
        message="sentinelas de bloco balanceados"
        if error is None
        else f"desbalanceamento de bloco: {error['reason']}",
        details={"error": error},
    )


def _check_noteref_balanced(body: str) -> GateCheck:
    depth = 0
    pos = 0
    bad = None
    tokens = sorted(
        [(m.start(), NOTEREF_OPEN) for m in re.finditer(re.escape(NOTEREF_OPEN), body)]
        + [(m.start(), NOTEREF_CLOSE) for m in re.finditer(re.escape(NOTEREF_CLOSE), body)]
    )
    for at, tok in tokens:
        if tok == NOTEREF_OPEN:
            depth += 1
        else:
            depth -= 1
            if depth < 0:
                bad = {"reason": "NOTEREF close sem open", "char_start": at}
                break
    if bad is None and depth != 0:
        bad = {"reason": "NOTEREF open sem close", "depth": depth}
    return GateCheck(
        code="noteref_balanced",
        passed=bad is None,
        message="NOTEREF balanceados"
        if bad is None
        else f"NOTEREF desbalanceado: {bad['reason']}",
        details={"error": bad},
    )


# --- C4 ---------------------------------------------------------------------

def _check_body_isolated(body: str, ctx: AnchorCtx) -> GateCheck:
    leaks = []
    for pat in ctx.residual_noise_patterns:
        rx = re.compile(pat)
        for line, start, _ in _line_offsets(body):
            if line.strip() in BLOCK_SENTINELS:
                continue
            if rx.search(line):
                leaks.append({"pattern": pat, "char_start": start, "line": line})
    return GateCheck(
        code="body_isolated",
        passed=not leaks,
        message="corpo isolado (zero ruído posicional residual)"
        if not leaks
        else f"{len(leaks)} vazamento(s) de ruído residual (C4)",
        details={"leaks": leaks},
    )


# --- C3/C5 parcial ----------------------------------------------------------

def _check_body_hashable(body: str) -> tuple[GateCheck, str | None]:
    try:
        body.encode("utf-8").decode("utf-8")
        body_hash = sha256_text(body)
    except UnicodeError as exc:  # pragma: no cover - str é sempre UTF-8 válido
        return (
            GateCheck("body_hashable", False, f"corpo não-hashável: {exc}", {}),
            None,
        )
    return (
        GateCheck(
            "body_hashable",
            True,
            "corpo é byte-sequence UTF-8 estável e hasheável",
            {"body_hash": body_hash},
        ),
        body_hash,
    )


# --- C6 ---------------------------------------------------------------------

def _check_provenance_frozen(ctx: AnchorCtx) -> GateCheck:
    prov = ctx.provenance_dict()
    missing = [
        k
        for k in ("source_format", "source_edition")
        if not prov.get(k)
    ]
    # translation_layer DEVE estar presente como chave (null explícito ≠ ausente)
    has_translation_key = "translation_layer" in prov
    passed = not missing and has_translation_key
    return GateCheck(
        code="provenance_frozen",
        passed=passed,
        message="proveniência congelada (formato/edição presentes, translation_layer presente)"
        if passed
        else f"proveniência incompleta: faltam {missing or ['translation_layer']}",
        details={"provenance": prov, "missing": missing},
    )


# --- C7 ---------------------------------------------------------------------

def _check_body_min_size(body: str, ctx: AnchorCtx) -> tuple[GateCheck, int]:
    size = count_body_size(body, ctx.size_unit)
    passed = size >= ctx.min_size
    return (
        GateCheck(
            code="body_min_size",
            passed=passed,
            message=f"tamanho do corpo {size} {ctx.size_unit} >= min {ctx.min_size}"
            if passed
            else f"corpo abaixo do mínimo: {size} < {ctx.min_size} {ctx.size_unit}",
            details={"size": size, "min_size": ctx.min_size, "unit": ctx.size_unit},
        ),
        size,
    )


# --- partes públicas --------------------------------------------------------

def validate_body_static(body: str, ctx: AnchorCtx) -> GateResult:
    """C2 + C4 + C3/C5(parcial) + C6 + C7 sobre o corpo, sem estrutura."""
    hashable_check, body_hash = _check_body_hashable(body)
    size_check, body_size = _check_body_min_size(body, ctx)
    checks = (
        _check_sentinels_isolated(body),      # C2
        _check_sentinels_balanced(body),      # C2
        _check_noteref_balanced(body),        # C2
        _check_body_isolated(body, ctx),      # C4
        hashable_check,                        # C3/C5 parcial
        _check_provenance_frozen(ctx),        # C6
        size_check,                            # C7
    )
    return GateResult(
        passed=all(c.passed for c in checks),
        contract_version=ctx.contract_version,
        body_hash=body_hash,
        body_size=body_size,
        checks=checks,
        diagnostics=_diagnostics(ctx, body_hash),
    )


def _check_roundtrip_axis(body: str, structure: dict) -> GateCheck:
    mismatches = []
    for idx, p in enumerate(structure.get("paragraphs", [])):
        got = strip_sentinels(body[p["char_start"]:p["char_end"]])
        if got != p["text"]:
            mismatches.append({"index": idx, "expected": p["text"], "got": got})
    return GateCheck(
        code="roundtrip_axis",
        passed=not mismatches,
        message="C1: strip_sentinels(span) == text para todo parágrafo"
        if not mismatches
        else f"C1 violado em {len(mismatches)} parágrafo(s)",
        details={"mismatches": mismatches[:10]},
    )


def _check_roundtrip_coverage(body: str, structure: dict) -> GateCheck:
    paras = structure.get("paragraphs", [])
    spans = sorted((p["char_start"], p["char_end"]) for p in paras)

    # overlap
    overlaps = []
    for (s0, e0), (s1, e1) in zip(spans, spans[1:]):
        if s1 < e0:
            overlaps.append({"prev_end": e0, "next_start": s1})

    # cobertura
    covered = [False] * len(body)
    for s, e in spans:
        for i in range(s, min(e, len(body))):
            covered[i] = True
    sentinel_mask = _sentinel_char_mask(body)

    gaps = []
    for i, ch in enumerate(body):
        if ch.isspace() or sentinel_mask[i]:
            continue
        if not covered[i]:
            gaps.append(i)

    passed = not overlaps and not gaps
    return GateCheck(
        code="roundtrip_coverage",
        passed=passed,
        message="C1': spans cobrem todo corpo não-sentinela sem overlap/gap"
        if passed
        else f"C1' violado: {len(overlaps)} overlap(s), {len(gaps)} char(s) em gap",
        details={"overlaps": overlaps[:10], "gap_positions": gaps[:20]},
    )


def validate_roundtrip(body: str, structure: dict, ctx: AnchorCtx) -> GateResult:
    """C1 (eixo) + C1' (cobertura)."""
    body_hash = sha256_text(body)
    checks = (
        _check_roundtrip_axis(body, structure),       # C1
        _check_roundtrip_coverage(body, structure),   # C1'
    )
    return GateResult(
        passed=all(c.passed for c in checks),
        contract_version=ctx.contract_version,
        body_hash=body_hash,
        body_size=count_body_size(body, ctx.size_unit),
        checks=checks,
        diagnostics=_diagnostics(ctx, body_hash),
    )
