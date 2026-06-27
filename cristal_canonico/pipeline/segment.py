"""segment — corpo canônico → structure.json (spec 4.3).

Máquina de estados: varre linhas; `block_stack` empilha em open-sentinel,
desempilha em close; `block_type` de cada parágrafo = topo da pilha
(`body` se vazia).

Rastreio de offset: cada parágrafo carrega char_start/char_end no body, e
`text` = strip_sentinels(body[char_start:char_end]). Como split("\\n")
remove o separador, char_end = char_start + len(line) e a próxima linha
começa em char_end + 1 — offsets exatos e reconstrutíveis (C3).

Invariante a manter: C1 (round-trip) e C1' (cobertura) — provados pelo
portão contra o golden.
"""

from __future__ import annotations

from core.sentinels import BLOCK_CLOSERS, BLOCK_OPENERS
from core.text import strip_sentinels

DEFAULT_CHAPTER_ID = "ch1"


def segment(body: str, ctx=None) -> dict:
    """Corpo canônico → dict no formato structure.json (spec 1.3)."""
    chapter_id = getattr(ctx, "chapter_id", None) or DEFAULT_CHAPTER_ID
    paragraphs: list[dict] = []
    block_stack: list[str] = []

    pos = 0
    for line in body.split("\n"):
        line_start = pos
        line_end = line_start + len(line)
        pos = line_end + 1  # +1 pelo "\n" que o split removeu

        stripped = line.strip()
        if stripped in BLOCK_OPENERS:
            block_stack.append(BLOCK_OPENERS[stripped])
            continue
        if stripped in BLOCK_CLOSERS:
            if block_stack:
                block_stack.pop()
            continue
        if stripped == "":
            continue  # branco: separador, não é parágrafo

        paragraphs.append(
            {
                "char_start": line_start,
                "char_end": line_end,
                "text": strip_sentinels(line),
                "block_type": block_stack[-1] if block_stack else "body",
                "chapter_id": chapter_id,
            }
        )

    return {"paragraphs": paragraphs}
