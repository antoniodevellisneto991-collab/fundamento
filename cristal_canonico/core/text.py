"""Funções núcleo de texto (spec 1.4) + modelo de freeze (spec 1.5).

Vocabulário compartilhado por todas as camadas. Determinístico, stdlib-only.
"""

from __future__ import annotations

import hashlib

from .sentinels import ALL_SENTINEL_TOKENS

# Defaults de tamanho mínimo (spec 1: "Fixar min_size + unidade").
# Produção usa estes; a fixture golden passa um ctx com min_size menor.
DEFAULT_SIZE_UNIT = "words"
DEFAULT_MIN_SIZE = 1000

# Versão da engine determinística (entra no freeze_key, spec 1.5).
ENGINE_VERSION = "0.1.0"


def strip_sentinels(s: str) -> str:
    """Remove todos os tokens de sentinela (bloco + noteref); devolve a prosa.

    Remove apenas os marcadores ⟦...⟧; o conteúdo entre um par NOTEREF
    (ex.: o número da nota) permanece como prosa. Como nenhum token é
    substring de outro (o '/' do closer impede), a ordem é irrelevante.
    """
    for token in ALL_SENTINEL_TOKENS:
        if token in s:
            s = s.replace(token, "")
    return s


def count_body_size(body: str, unit: str = DEFAULT_SIZE_UNIT) -> int:
    """Tamanho do conteúdo de corpo (sentinelas removidos).

    unit ∈ {"words", "chars"}. Medido sobre a prosa, não sobre o bruto com
    sentinelas — coerente com C7 (tamanho medido no corpo).
    """
    prose = strip_sentinels(body)
    if unit == "words":
        return len(prose.split())
    if unit == "chars":
        return len(prose.strip())
    raise ValueError(f"unit inválida: {unit!r} (esperado 'words' ou 'chars')")


def sha256_text(s: str) -> str:
    """Hash determinístico do texto UTF-8."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def freeze_key(
    raw_hash: str,
    anchor_filling_hash: str | None,
    engine_version: str = ENGINE_VERSION,
) -> str:
    """Chave de reprodutibilidade (spec 1.5).

    (raw_hash, anchor_filling_hash, engine_version) -> chave determinística.
    Re-rodar a mesma âncora sobre o mesmo bruto com a mesma engine dá o
    mesmo body_hash; esta chave indexa esse determinismo.
    """
    filling = anchor_filling_hash if anchor_filling_hash is not None else "∅"
    return sha256_text(f"{raw_hash}\x1f{filling}\x1f{engine_version}")
