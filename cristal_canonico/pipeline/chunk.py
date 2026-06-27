"""chunk — estrutura → chunks com sinal (spec 4.4).

Boundary argumentativa aproximada por acúmulo guloso de parágrafos: alvo
~750 chars, máximo ~1100 (parâmetros via ctx). A janela grande absorve a
necessidade estilométrica de jusante sem boundary separada.

Emite por chunk: `block_types` (multiset), `quote_ratio` (fração de
blockquote, por chars) e `footnote_ratio`. Quando os sentinelas chegam,
esses campos carregam o sinal de voz-alheia; quando não chegam (PDF/TXT),
ficam em `body`/0.0 — latentes, não defeituosos.

Parâmetros formais (trilha estilométrica): alvo ~2200 chars (~350 palavras),
máximo ~3500. Estilometria e curadoria de voz exigem janelas maiores para
que a assinatura de função-palavra e os n-gramas de POS sejam estáveis.
"""

from __future__ import annotations

from collections import Counter

DEFAULT_TARGET_CHARS = 750
DEFAULT_MAX_CHARS = 1100

# Trilha formal (estilometria / curadoria de voz).
# Alvo ≥ 350 palavras PT (~2275 chars) para estabilidade de Burrows Delta,
# TTR/MATTR e n-gramas de POS — alinhado com quality.py do sistema anterior.
FORMAL_TARGET_CHARS = 2200
FORMAL_MAX_CHARS = 3500


def _ratio(paras: list[dict], block_type: str, total_chars: int) -> float:
    if total_chars <= 0:
        return 0.0
    hit = sum(len(p["text"]) for p in paras if p["block_type"] == block_type)
    return hit / total_chars


def _make_chunk(chunk_id: int, paras: list[dict]) -> dict:
    total_chars = sum(len(p["text"]) for p in paras)
    block_types = Counter(p["block_type"] for p in paras)
    return {
        "chunk_id": chunk_id,
        "char_start": paras[0]["char_start"],
        "char_end": paras[-1]["char_end"],
        "n_paragraphs": len(paras),
        "text": "\n\n".join(p["text"] for p in paras),
        "block_types": dict(block_types),
        "quote_ratio": _ratio(paras, "blockquote", total_chars),
        "footnote_ratio": _ratio(paras, "footnote", total_chars),
        "chapter_id": paras[0].get("chapter_id"),
        # Spans dos parágrafos constituintes, em ordem. Para a janela formal
        # filtrada (só voz do autor), o intervalo [char_start:char_end] deixa de
        # ser contíguo — pula citações/notas removidas. Estes spans preservam a
        # proveniência exata: o chunk é reconstruível parágrafo a parágrafo.
        "paragraph_spans": [[p["char_start"], p["char_end"]] for p in paras],
    }


def chunk(structure: dict, ctx=None, keep_block_types=None) -> list[dict]:
    """structure.json → lista de chunks com sinal.

    keep_block_types: se dado (conjunto), só parágrafos com `block_type` no
    conjunto entram nas janelas. Usado pela trilha FORMAL com `{"body"}` —
    para voz, só o autor: citação (blockquote) e nota (footnote) ficam fora
    ANTES de janelar, impedindo a mistura por construção. A janela conceitual
    chama sem filtro (citação revela engajamento conceitual).
    """
    target = getattr(ctx, "chunk_target_chars", None) or DEFAULT_TARGET_CHARS
    max_chars = getattr(ctx, "chunk_max_chars", None) or DEFAULT_MAX_CHARS

    paragraphs = structure.get("paragraphs", [])
    if keep_block_types is not None:
        paragraphs = [p for p in paragraphs if p["block_type"] in keep_block_types]
    chunks: list[dict] = []
    cur: list[dict] = []
    cur_chars = 0
    next_id = 0

    def flush() -> None:
        nonlocal cur, cur_chars, next_id
        if cur:
            chunks.append(_make_chunk(next_id, cur))
            next_id += 1
            cur = []
            cur_chars = 0

    for para in paragraphs:
        plen = len(para["text"])
        # estoura o máximo? fecha o chunk atual antes de adicionar
        if cur and cur_chars + plen > max_chars:
            flush()
        cur.append(para)
        cur_chars += plen
        # atingiu o alvo? fecha em boundary de parágrafo
        if cur_chars >= target:
            flush()

    flush()
    return chunks
