"""Repertório canônico de ruído editorial (fase canônica).

Padrões conservadores derivados de inspeção empírica do corpus real
(ver phase_canonic_inventory.md). Cada padrão é um regex testado linha a
linha pela preclean (stage 1 — strip_header_footer).

Critério de inclusão: evidência forte no corpus + risco baixo de falso
positivo em prosa autoral. Padrões ambíguos (ex.: SUMÁRIO, front matter
genérico) ficam de fora desta rodada.
"""

from __future__ import annotations

import hashlib

# Repertório conservador de ruído editorial comum.
# Ordem não altera semântica; mantida por categoria para legibilidade.
CANONICAL_NOISE_PATTERNS: tuple[str, ...] = (
    # URLs de digitalização / distribuição informal
    r"https?://\S+",
    r"\bwww\.\S+\.\S+",
    # Marcadores de paginação OCR injetados na conversão (ex.: [pág. 9])
    r"\[pág\.\s*\d+\]",
    # Linhas de ISBN (praticamente nunca aparecem em prosa autoral)
    r"\bISBN\b",
    # Números de classificação bibliográfica CDD / CDU
    r"\bCDD\b",
    r"\bCDU\b",
    # Cabeçalho de bloco de catalogação CIP
    r"Dados Internacionais de Catalogação",
    r"Câmara Brasileira do Livro",
    # Propaganda editorial cross-sell (fim de EPUB)
    r"Compre agora e leia",
)


def repertoire_hash(patterns: tuple[str, ...]) -> str:
    """Hash SHA-256 estável do repertório — entra em anchor_filling_hash.

    Permite detectar quando o repertório mudou e o catálogo precisa de
    re-bake (o freeze_key muda junto com anchor_filling_hash).
    """
    joined = "\x1f".join(patterns)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
