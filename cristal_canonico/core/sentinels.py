"""Vocabulário de sentinela (spec 1.1).

Marcadores estruturais que sobrevivem inertes pelo tronco e sinalizam
voz-alheia. Tokens exatos — a grafia pode trocar, a estrutura não.

Regra de ouro: sentinelas de bloco ficam SOZINHOS na linha (condição que
permite preservá-los por igualdade-exata através de qualquer limpeza).
NOTEREF inline é par balanceado dentro da prosa.
"""

# Tokens de bloco (nomeados, para uso direto pelo extract)
BQ_OPEN, BQ_CLOSE = "⟦PRL:BQ⟧", "⟦/PRL:BQ⟧"
FN_OPEN, FN_CLOSE = "⟦PRL:FN⟧", "⟦/PRL:FN⟧"

# Referência inline a nota (par balanceado dentro da prosa)
NOTEREF_OPEN, NOTEREF_CLOSE = "⟦PRL:REF⟧", "⟦/PRL:REF⟧"

# Bloco — papel -> tipo
BLOCK_OPENERS = {BQ_OPEN: "blockquote", FN_OPEN: "footnote"}
BLOCK_CLOSERS = {BQ_CLOSE: "blockquote", FN_CLOSE: "footnote"}
BLOCK_SENTINELS = set(BLOCK_OPENERS) | set(BLOCK_CLOSERS)

# open -> close (bloco) para checagem de balanceamento
BLOCK_PAIR = {BQ_OPEN: BQ_CLOSE, FN_OPEN: FN_CLOSE}

# Todos os tokens de sentinela (bloco + noteref) — usado por strip_sentinels.
ALL_SENTINEL_TOKENS = tuple(BLOCK_SENTINELS) + (NOTEREF_OPEN, NOTEREF_CLOSE)
