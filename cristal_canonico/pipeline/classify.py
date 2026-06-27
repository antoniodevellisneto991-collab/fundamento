"""Classificação estrutural de blocos da fase canônica (pré-trilhas).

Rotula CADA bloco do corpo segmentado em uma de cinco classes mínimas, de
forma determinística e sem LLM, para que as trilhas a jusante recebam um corpo
já organizado — navegação fora, paratexto preservado, e citação/nota
identificadas em vez de fundidas no corpo de forma caótica.

    1. navigation_noise   — capa/rosto/sumário/landmarks + corrida de TOC.
    2. editorial_discourse— paratexto discursivo (prefácio, introdução, nota…).
    3. main_body          — prosa do corpo principal.
    4. quotation_material — citação em bloco (voz alheia).
    5. footnote_material  — nota de rodapé/fim.

Princípio arquitetural: USAR a estrutura que já existe, não adivinhar.

* Citação e nota já chegam marcadas por SENTINELA desde o `extract`
  (⟦PRL:BQ⟧ / ⟦PRL:FN⟧) e o `segment` já as expõe como `block_type`
  blockquote/footnote. Essa é a evidência estrutural — `quotation_material`
  e `footnote_material` saem DAÍ, não de regex sobre a superfície do texto.
* Navegação vs. discurso vs. corpo sai do classificador de regime de
  `front_matter` (corrida de títulos sem prosa vs. prosa contínua), o MESMO
  cérebro usado pela poda de front matter no preclean — fonte única.

Decisão explícita: NÃO se infere citação/nota por padrão de superfície
(bloco entre aspas, corrida de linhas numeradas). A inspeção do corpus real
mostrou que esse chute mistura CORPO em nota — listas numeradas de
bibliografia, pontos enumerados do argumento, entradas de diário com data
("3 ABR. ...") batem no mesmo padrão de uma nota. Como o critério é "não
contaminar o corpo", o sinal de superfície faz mais mal que bem: o caminho
correto de melhora é a marcação a montante (extract), não o palpite a jusante.
A chamada de nota inline (NOTEREF) é registrada como METADADO estrutural
(`has_note_call`) — sinal, não reclassificação.

Não mexe em `segment` nem em `chunk`: consome `structure.json` (+ o corpo,
para o metadado de chamada de nota) e devolve uma lista de classes alinhada a
`structure["paragraphs"]`.
"""

from __future__ import annotations

from core.sentinels import NOTEREF_OPEN
from pipeline.front_matter import (
    EDITORIAL_DISCOURSE,
    FRONT_ZONE_BLOCKS,
    MAIN_BODY_START,
    MIN_NAV_RUN,
    NAVIGATION_NOISE,
    classify_front_matter,
)

# Classes (strings — JSON-friendly). Reexporta as de front_matter + as novas.
MAIN_BODY = "main_body"
QUOTATION_MATERIAL = "quotation_material"
FOOTNOTE_MATERIAL = "footnote_material"

ALL_CLASSES = (
    NAVIGATION_NOISE,
    EDITORIAL_DISCOURSE,
    MAIN_BODY,
    QUOTATION_MATERIAL,
    FOOTNOTE_MATERIAL,
)

CLASSIFY_VERSION = "0.2.0"


def classify_structure(
    structure: dict,
    *,
    min_run: int = MIN_NAV_RUN,
    front_zone: int = FRONT_ZONE_BLOCKS,
) -> list[str]:
    """structure.json -> lista de classes, uma por parágrafo (mesma ordem).

    Determinístico. Ordem de decisão (do mais estrutural ao default):

    1. sentinela (block_type): blockquote -> quotation; footnote -> footnote.
    2. regime de front matter sobre a SEQUÊNCIA de blocos de corpo.
    3. default: main_body.
    """
    paras = structure.get("paragraphs", [])
    classes: list[str | None] = [None] * len(paras)

    # (1) Classe estrutural por sentinela — única fonte de citação/nota.
    body_idx: list[int] = []   # índices globais dos parágrafos de corpo
    body_txt: list[str] = []
    for i, p in enumerate(paras):
        bt = p.get("block_type")
        if bt == "blockquote":
            classes[i] = QUOTATION_MATERIAL
        elif bt == "footnote":
            classes[i] = FOOTNOTE_MATERIAL
        else:
            body_idx.append(i)
            body_txt.append(p.get("text", ""))

    # (2) Regime de front matter sobre os blocos de corpo (mesmo cérebro do
    # preclean). Só rotula o começo: navegação / paratexto / início do corpo.
    fm = classify_front_matter(body_txt, min_run=min_run, front_zone=front_zone)
    for bi, label in fm.items():
        gi = body_idx[bi]
        if label == NAVIGATION_NOISE:
            classes[gi] = NAVIGATION_NOISE
        elif label == EDITORIAL_DISCOURSE:
            classes[gi] = EDITORIAL_DISCOURSE
        elif label == MAIN_BODY_START:
            classes[gi] = MAIN_BODY

    # (3) Default — prosa do corpo.
    return [c if c is not None else MAIN_BODY for c in classes]


def classification_records(
    structure: dict, classes: list[str], body: str | None = None
) -> list[dict]:
    """Junta cada parágrafo à sua classe — registro do artefato (pré-trilhas).

    `has_note_call`: o span carrega uma chamada de nota inline (NOTEREF) — sinal
    estrutural de "este bloco de corpo referencia uma nota", sem mudar a classe.
    """
    paras = structure.get("paragraphs", [])
    total = len(paras)
    out: list[dict] = []
    for i, (p, cls) in enumerate(zip(paras, classes)):
        has_note_call = bool(
            body and NOTEREF_OPEN in body[p["char_start"]:p["char_end"]]
        )
        out.append(
            {
                "index": i,
                "char_start": p["char_start"],
                "char_end": p["char_end"],
                "block_type": p.get("block_type"),
                "block_class": cls,
                "has_note_call": has_note_call,
                "chapter_id": p.get("chapter_id"),
                "position": round(i / total, 4) if total else 0.0,
            }
        )
    return out


def class_counts(classes: list[str]) -> dict[str, int]:
    """Contagem por classe (resumo determinístico, ordem fixa)."""
    return {c: classes.count(c) for c in ALL_CLASSES if classes.count(c)}
