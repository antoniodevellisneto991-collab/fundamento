"""Elegibilidade de chunk por trilha — limpeza de Nível 2, pós-portão.

Esta é a camada de limpeza ESPECÍFICA por trilha (Nível 2), distinta da
limpeza canônica/genérica (Nível 1, em `preclean.py` + `noise.py`, antes do
portão). O portão certifica um corpo genérico; cada trilha define aqui seus
critérios de elegibilidade sobre os chunks já certificados — sem reprocessar o
bruto. Assim um só `book.body.txt` alimenta trilhas diferentes.

DECISÃO (2026-06-22): a limpeza de Nível 2 age DEPOIS do portão, sobre os
chunks certificados (opção B), não sobre o bruto antes do portão (opção A).
Esta função vivia enterrada dentro de `conceptual.py` (`_is_noise_chunk`); foi
elevada a etapa nomeada por esta cirurgia arquitetural — comportamento idêntico
ao original, apenas reposicionada.

Os critérios ainda misturam dois planos (resíduo genérico de paratexto vs.
classe dominante específica da trilha conceitual). A separação dos limiares por
trilha é refinamento posterior; aqui só se move, não se altera.
"""

from __future__ import annotations

import re
from collections import Counter

# Mesmo tokenizador da trilha conceitual — duplicado aqui para manter o módulo
# autocontido (evita import circular conceitual <-> chunk_eligibility).
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'_-]*")


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in WORD_RE.finditer(text)]


def chunk_class_profile(chunk: dict, classes: list[dict]) -> dict[str, float]:
    """Perfil de classes do chunk: fração de sobreposição por block_class.

    Cruza o span do chunk com os registros de `classification.jsonl` e devolve,
    para cada classe estrutural tocada, a fração do chunk coberta por ela.
    """
    counts: Counter[str] = Counter()
    total = 0
    for rec in classes:
        overlap = min(chunk["char_end"], rec["char_end"]) - max(
            chunk["char_start"], rec["char_start"]
        )
        if overlap <= 0:
            continue
        counts[rec["block_class"]] += overlap
        total += overlap
    if total <= 0:
        return {}
    return {key: value / total for key, value in counts.items()}


# --- Nível 2 — limiares de classe POR TRILHA ---------------------------------
# Cada trilha define quanto de cada classe estrutural torna um chunk inelegível
# para ELA. Agora independentes: tunar a formal não toca a conceitual. Valores
# iniciais da formal = conceitual (separação arquitetural primeiro, tuning com
# evidência depois — ver [[verify-not-validate]]). Para a formal, citação e nota
# já são removidas ANTES de janelar (keep_block_types={"body"} em chunk.py), logo
# os limiares quote/footnote aqui são defensivos/redundantes, não o filtro real.
TRAIL_THRESHOLDS: dict[str, dict[str, float]] = {
    "conceitual": {
        "navigation_noise": 0.2,
        "editorial_discourse": 0.45,
        "quotation_material": 0.55,
        "quote_ratio": 0.5,
        "footnote_material": 0.35,
        "footnote_ratio": 0.2,
        "min_tokens": 18,
    },
    "formal": {
        "navigation_noise": 0.2,
        "editorial_discourse": 0.45,
        "quotation_material": 0.55,
        "quote_ratio": 0.5,
        "footnote_material": 0.35,
        "footnote_ratio": 0.2,
        "min_tokens": 18,
    },
}


def _generic_paratext_noise(chunk: dict) -> tuple[bool, str | None]:
    """Nível 1 (compartilhado): resíduo de paratexto/front-matter editorial.

    Independe de trilha — copyright, ficha catalográfica, sumário, página de
    tradução. Seria ruído para qualquer leitor do corpo.
    """
    text = chunk["text"].lower()
    paragraphs = [p.strip() for p in chunk["text"].split("\n\n") if p.strip()]
    avg_para_len = (
        sum(len(p) for p in paragraphs) / len(paragraphs) if paragraphs else len(chunk["text"])
    )
    uppercase_ratio = (
        sum(1 for c in chunk["text"] if c.isupper()) / max(len(chunk["text"]), 1)
    )
    if "all rights reserved" in text or "copyright" in text:
        return True, "paratexto editorial/copyright"
    if any(
        marker in text
        for marker in (
            "library of congress",
            "cataloging in publication",
            "preface",
            "contents",
            "dedication",
            "permissions acknowledgments",
            "published in the united states",
            "pantheon books",
            "includes bibliographical references",
            "isbn ",
        )
    ):
        return True, "front matter residual"
    # cross-sell editorial de epub (PT e EN) — não é voz do autor nem conteúdo
    if any(
        marker in text
        for marker in (
            "compre agora e leia",
            "discover your next great read",
            "personalized book picks",
            "sign up now",
        )
    ):
        return True, "cross-sell/paratexto editorial"
    if chunk.get("chunk_id", 9999) <= 2 and any(
        marker in text
        for marker in ("tradução", "traducao", "translation", "translated by")
    ):
        return True, "front matter residual"
    if (
        chunk.get("chunk_id", 9999) <= 4
        and len(paragraphs) >= 3
        and avg_para_len <= 85
        and (
            uppercase_ratio >= 0.12
            or any(
                marker in text
                for marker in (
                    "tradução", "traducao", "translation", "translated by",
                    "sumário", "contents", "prefácio", "preface", "isbn",
                )
            )
        )
    ):
        return True, "front matter residual"
    return False, None


def _trail_class_noise(
    chunk: dict, profile: dict[str, float], th: dict[str, float]
) -> tuple[bool, str | None]:
    """Nível 2 (por trilha): classe estrutural dominante torna o chunk inelegível."""
    if profile.get("navigation_noise", 0.0) >= th["navigation_noise"]:
        return True, "navegação ou embalagem editorial"
    if profile.get("editorial_discourse", 0.0) >= th["editorial_discourse"]:
        return True, "discurso editorial dominante"
    if (
        profile.get("quotation_material", 0.0) >= th["quotation_material"]
        or chunk.get("quote_ratio", 0.0) >= th["quote_ratio"]
    ):
        return True, "citação dominante"
    if (
        profile.get("footnote_material", 0.0) >= th["footnote_material"]
        or chunk.get("footnote_ratio", 0.0) >= th["footnote_ratio"]
    ):
        return True, "nota dominante"
    return False, None


def is_noise_chunk(
    chunk: dict, profile: dict[str, float], trilha: str = "conceitual"
) -> tuple[bool, str | None]:
    """Decide se um chunk é inelegível para a trilha dada.

    Composição de três filtros, NA ORDEM (preserva a razão retornada):
    1. paratexto genérico (Nível 1, compartilhado);
    2. classe dominante por trilha (Nível 2, limiares de `TRAIL_THRESHOLDS`);
    3. curto demais (Nível 1, piso de tokens — tunável por trilha).

    `trilha="conceitual"` reproduz exatamente o comportamento original.
    """
    if trilha not in TRAIL_THRESHOLDS:
        raise ValueError(f"trilha desconhecida: {trilha!r} (use {list(TRAIL_THRESHOLDS)})")
    th = TRAIL_THRESHOLDS[trilha]

    hit, reason = _generic_paratext_noise(chunk)
    if hit:
        return hit, reason
    hit, reason = _trail_class_noise(chunk, profile, th)
    if hit:
        return hit, reason
    if len(_tokenize(chunk["text"])) < th["min_tokens"]:
        return True, "bloco curto demais para unidade analítica estável"
    return False, None
