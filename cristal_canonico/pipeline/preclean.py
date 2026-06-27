"""preclean canônico — bruto → corpo canônico (spec 4.2).

Este é o PREENCHIMENTO TRIVIAL da âncora: preclean + cp body = a rota
canônica (Degrau 2 da escada de dispatch).

Limpeza determinística em quatro estágios: strip de cabeçalho/rodapé
corrente, remoção de número de página, de-hyphenation, reflow de linhas
quebradas.

A parte que NÃO pode falhar — proteção de sentinela por igualdade-exata:
em CADA estágio, se `line.strip() in BLOCK_SENTINELS`, a linha passa
literal. Nunca vira cabeçalho, nunca é removida, fica como bloco próprio.
Quatro pontos de proteção, um por estágio.

`ctx` é duck-typed (evita ciclo pipeline→anchor): lê opcionalmente
`residual_noise_patterns` (regex posicionais de ruído).
"""

from __future__ import annotations

import re

from core.sentinels import BLOCK_SENTINELS
from core.text import sha256_text
from pipeline.front_matter import (
    FRONT_ZONE_BLOCKS,
    MIN_NAV_RUN,
    NAVIGATION_NOISE,
    classify_front_matter,
)

PRECLEAN_VERSION = "0.1.0"

# Linha que é só número de página (com travessões/espaços opcionais).
_PAGE_NUM_RE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")
# Hífen de quebra no fim da linha (entre caractere de palavra e fim).
_TRAILING_HYPHEN_RE = re.compile(r"(\w)-\s*$")
# Pontuação que encerra uma frase (reflow não junta após ela).
_TERMINALS = (".", "!", "?", ":", ";", "”", "\"", ")", "]", "…")


def _is_sentinel(line: str) -> bool:
    return line.strip() in BLOCK_SENTINELS


def _stage_trim_front_matter(lines: list[str], ctx) -> list[str]:
    """Estágio 0 (opt-in) — remove navegação/embalagem do começo do corpo.

    Mapeia linhas -> blocos (linhas de conteúdo, ignorando brancos), classifica
    o front zone com `classify_front_matter` e descarta os blocos rotulados
    `navigation_noise`. Conservador por design: só age no começo, só remove
    corrida de navegação com marcador estrutural forte (ver front_matter.py).

    Proteção de sentinela: uma linha de sentinela ENCERRA o front zone — nunca
    é candidata a bloco, nunca é removida, e impede o trim de entrar em
    blockquote/nota (preserva C2).
    """
    if not getattr(ctx, "trim_front_matter", False):
        return lines

    min_run = getattr(ctx, "front_matter_min_run", None) or MIN_NAV_RUN
    front_zone = getattr(ctx, "front_matter_zone", None) or FRONT_ZONE_BLOCKS

    block_lines: list[int] = []   # índices de linha que são blocos de conteúdo
    block_texts: list[str] = []
    for idx, line in enumerate(lines):
        if _is_sentinel(line):
            break                 # sentinela encerra o front zone (proteção)
        if line.strip() == "":
            continue              # branco é separador, não bloco
        block_lines.append(idx)
        block_texts.append(line.strip())
        if len(block_texts) >= front_zone:
            break

    labels = classify_front_matter(
        block_texts, min_run=min_run, front_zone=front_zone
    )
    drop = {
        block_lines[bi]
        for bi, label in labels.items()
        if label == NAVIGATION_NOISE
    }
    if not drop:
        return lines

    out: list[str] = []
    for idx, line in enumerate(lines):
        if idx in drop:
            continue
        # colapsa brancos consecutivos resultantes da remoção
        if line.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(line)
    # remove brancos líderes deixados pela poda do topo
    while out and out[0].strip() == "":
        out.pop(0)
    return out


def _stage_strip_header_footer(lines: list[str], patterns: tuple) -> list[str]:
    """Estágio 1 — remove linhas de ruído posicional (cabeçalho/rodapé)."""
    out: list[str] = []
    for line in lines:
        if _is_sentinel(line):          # proteção 1
            out.append(line)
            continue
        if any(p.search(line) for p in patterns):
            continue
        out.append(line)
    return out


def _stage_remove_page_numbers(lines: list[str]) -> list[str]:
    """Estágio 2 — remove linhas que são apenas número de página."""
    out: list[str] = []
    for line in lines:
        if _is_sentinel(line):          # proteção 2
            out.append(line)
            continue
        if _PAGE_NUM_RE.match(line):
            continue
        out.append(line)
    return out


def _stage_dehyphenate(lines: list[str]) -> list[str]:
    """Estágio 3 — junta palavra hifenizada quebrada entre linhas."""
    work = list(lines)
    out: list[str] = []
    i = 0
    n = len(work)
    while i < n:
        line = work[i]
        if _is_sentinel(line):          # proteção 3
            out.append(line)
            i += 1
            continue
        m = _TRAILING_HYPHEN_RE.search(line)
        if m and i + 1 < n and not _is_sentinel(work[i + 1]) and work[i + 1].strip():
            merged = _TRAILING_HYPHEN_RE.sub(r"\1", line) + work[i + 1].lstrip()
            work[i + 1] = merged        # encadeia merges subsequentes
            i += 1
            continue
        out.append(line)
        i += 1
    return out


def _stage_reflow(lines: list[str]) -> list[str]:
    """Estágio 4 — junta linha quebrada à anterior (conservador).

    Junta só quando a anterior não termina em pontuação final E a atual
    começa minúscula — sinal forte de quebra dura no meio de frase. Nunca
    cruza sentinela.
    """
    out: list[str] = []
    for line in lines:
        if _is_sentinel(line):          # proteção 4
            out.append(line)
            continue
        s = line.strip()
        if s == "":
            out.append(line)
            continue
        if out and not _is_sentinel(out[-1]) and out[-1].strip():
            prev = out[-1].rstrip()
            if prev and prev[-1] not in _TERMINALS and s[:1].islower():
                out[-1] = prev + " " + s
                continue
        out.append(line)
    return out


def preclean(raw_text: str, ctx=None) -> tuple[str, dict]:
    """Bruto → corpo canônico + report (version + output_sha256)."""
    raw_patterns = getattr(ctx, "residual_noise_patterns", ()) or ()
    patterns = tuple(re.compile(p) for p in raw_patterns)

    lines = raw_text.split("\n")
    lines = _stage_trim_front_matter(lines, ctx)
    lines = _stage_strip_header_footer(lines, patterns)
    lines = _stage_remove_page_numbers(lines)
    lines = _stage_dehyphenate(lines)
    lines = _stage_reflow(lines)
    body = "\n".join(lines)

    report = {
        "version": PRECLEAN_VERSION,
        "output_sha256": sha256_text(body),
    }
    return body, report
