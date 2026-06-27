"""Classificação estrutural de front matter (fase canônica).

Decide o que remover do COMEÇO do corpo antes de `book.body.txt`: navegação
e embalagem do EPUB (capa, rosto, sumário, índice de capítulos) sem apagar
discurso editorial real (prefácio, introdução, nota do tradutor, apresentação).

NÃO usa LLM, NÃO depende de lista gigante de palavras mágicas e é
determinístico. O peso da decisão está em sinais COMPOSTOS e numa leitura de
REGIME, não num dicionário de títulos:

    Insight central
    ---------------
    Um sumário/índice é uma SEQUÊNCIA de blocos-título sem prosa entre eles.
    O corpo é o oposto: títulos SEPARADOS por prosa discursiva. A mudança de
    regime — "lista estrutural" -> "prosa contínua" — é o discriminador.
    Por isso um `Capítulo I` isolado (cercado de prosa) é preservado como
    título autoral, enquanto vinte `Capítulo I..XX` em fila são navegação.

Três classes de saída para os blocos iniciais:

* ``navigation_noise``   — capa/rosto/sumário/landmarks + a corrida de TOC.
* ``editorial_discourse``— paratexto discursivo real (prefácio, introdução…).
* ``main_body_start``    — o primeiro bloco do corpo contínuo.

A camada é CONSERVADORA por contrato: na dúvida, preserva. Só remove uma
corrida líder de navegação que carregue um marcador estrutural forte
(rótulo de sumário, capítulo numerado ou linha de TOC com paginação) — nunca
uma simples sequência de linhas curtas (que poderia ser um poema ou elenco).

Puro e stdlib-only. A fronteira de offsets (C1/C1') é re-derivada pelo
segment sobre o corpo final, então remover linhas inteiras aqui é seguro.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Rótulos de saída (strings simples — JSON-friendly, sem Enum).
NAVIGATION_NOISE = "navigation_noise"
EDITORIAL_DISCOURSE = "editorial_discourse"
MAIN_BODY_START = "main_body_start"

# --- parâmetros (determinísticos; entram na proveniência via signature) -----
FRONT_ZONE_BLOCKS = 80      # só os primeiros N blocos são candidatos a trim
MIN_NAV_RUN = 4             # corrida mínima de blocos-nav p/ virar navegação
HEADING_MAX_WORDS = 20      # teto de palavras p/ um bloco ser "título/linha"
DISCURSIVE_MIN_WORDS = 25   # piso de palavras p/ prosa longa contar como discurso
MULTI_SENTENCE_MIN_WORDS = 12  # prosa multifrásica também precisa de corpo mínimo

FRONT_MATTER_VERSION = "0.1.0"

# Rótulos ESTRUTURAIS de navegação/embalagem — conjunto pequeno e fechado.
# São sempre ruído quando aparecem como bloco isolado (não é "discurso").
# Comparados sobre a forma normalizada (sem acento, casefold).
_STRUCTURAL_LABELS = frozenset({
    "sumario", "indice", "indice geral", "indice de materias", "indice remissivo",
    "indice onomastico", "conteudo", "conteudos",
    "capa", "rosto", "folha de rosto", "pagina de rosto", "falsa folha de rosto",
    "landmarks", "guia", "marcadores",
    "lista de figuras", "lista de tabelas", "lista de ilustracoes",
    "lista de abreviaturas", "lista de mapas",
    "cover", "title page", "table of contents", "contents", "toc",
    "copyright", "copyright page", "creditos", "creditos editoriais",
    "ficha catalografica", "ficha tecnica",
})

# Prefixos de paratexto EDITORIAL — usados SÓ para enviesar à PRESERVAÇÃO
# (nunca à remoção). Marcam um cabeçalho discursivo real quando ele entra no
# corpo. A mesma palavra pode aparecer numa entrada de TOC (e aí cai pela
# regra de regime, não por estar nesta lista).
_EDITORIAL_PREFIXES = (
    "prefacio", "introducao", "apresentacao", "nota do tradutor",
    "nota da tradutora", "nota dos tradutores", "nota da edicao",
    "nota do editor", "nota do autor", "nota previa", "nota a edicao",
    "prologo", "posfacio", "esclarecimentos", "advertencia",
    "abertura", "ao leitor",
)

# Conectivos discursivos — sinal de prosa de verdade (não título/lista).
_CONNECTIVES = (
    "porque", "portanto", "contudo", "todavia", "entretanto", "no entanto",
    "porem", "embora", "ou seja", "isto e", "por exemplo", "alem disso",
    "dessa forma", "desse modo", "enquanto", "ainda que", "de modo que",
    "por isso", "uma vez que", "assim como", "tanto que", "de maneira que",
)
_CONNECTIVE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in _CONNECTIVES) + r")\b"
)

# Capítulo/parte numerados (sobre texto normalizado).
_CHAPTER_RE = re.compile(
    r"^(?:capitulo|cap\.?|parte|chapter|part|livro|tomo|secao|titulo)\b"
    r"(?:\s+(?:[ivxlcdm]+|\d+)\b)?",
    re.IGNORECASE,
)
_ORDINAL_PART_RE = re.compile(
    r"^(?:primeir|segund|terceir|quart|quint|sext|setim|oitav|non|decim)[oa]\b"
    r"\s+parte\b",
    re.IGNORECASE,
)
# Linha de TOC com paginação: líder pontilhado seguido de número, ou número
# de página solto no fim (ex.: "Nós, vitorianos ........... 9").
_TOC_LEADER_RE = re.compile(r"(?:\.{2,}|\.(?:\s\.){2,}|…)\s*\d{1,4}\s*$")
# Colapsa líder pontilhado / reticências em um único ponto (p/ contar frases).
_DOT_RUN_RE = re.compile(r"\.(?:\s*\.)+")
_SENTENCE_END = ".!?…"


def _normalize(text: str) -> str:
    """casefold + remoção de acentos + colapso de espaços (chave de rótulo)."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


@dataclass(frozen=True)
class BlockSignals:
    """Sinais determinísticos de um bloco do front matter."""

    text: str
    word_count: int
    sentences: int
    ends_sentence: bool
    has_connective: bool
    is_structural_label: bool
    is_editorial_heading: bool
    is_numbered_chapter: bool
    has_toc_leader: bool
    is_discursive: bool
    is_heading_like: bool
    is_nav_candidate: bool
    is_strong_nav_marker: bool
    is_section_heading: bool


def signals(text: str) -> BlockSignals:
    """Computa os sinais compostos de um bloco (puro, barato)."""
    norm = _normalize(text)
    wc = len(text.split())
    # Conta frases sobre o texto SEM o líder pontilhado/reticências — senão os
    # pontos de um sumário ("Nós, vitorianos ..... 9") viram "frases".
    sentences = sum(_DOT_RUN_RE.sub(".", text).count(c) for c in _SENTENCE_END)

    stripped = text.rstrip().rstrip("\"'”’)]»")
    ends_sentence = bool(stripped) and stripped[-1] in _SENTENCE_END
    has_connective = bool(_CONNECTIVE_RE.search(norm))

    is_structural_label = norm in _STRUCTURAL_LABELS
    is_numbered_chapter = bool(_CHAPTER_RE.match(norm) or _ORDINAL_PART_RE.match(norm))
    has_toc_leader = bool(_TOC_LEADER_RE.search(text))

    # Prosa discursiva real: longa com pontuação, OU multifrásica COM corpo, OU
    # média com conectivo. É o sinal que quebra a corrida de navegação. Uma
    # linha de TOC (líder pontilhado) NUNCA é discurso, ainda que tenha pontos
    # ou um "1." inicial. Exigir corpo mínimo na regra multifrásica evita que
    # iniciais ("J. A. Guilhon") contem como duas frases.
    is_discursive = not has_toc_leader and (
        (wc >= DISCURSIVE_MIN_WORDS and sentences >= 1)
        or (sentences >= 2 and wc >= MULTI_SENTENCE_MIN_WORDS)
        or (has_connective and wc >= DISCURSIVE_MIN_WORDS - 5)
    )

    # Linha curta sem ponto final e sem conectivo = título/linha de embalagem.
    is_heading_like = (
        not is_discursive
        and not ends_sentence
        and not has_connective
        and 1 <= wc <= HEADING_MAX_WORDS
    )

    is_editorial_heading = (
        not is_discursive
        and wc <= HEADING_MAX_WORDS
        and any(norm.startswith(p) for p in _EDITORIAL_PREFIXES)
    )

    is_strong_nav_marker = is_structural_label or is_numbered_chapter or has_toc_leader
    is_nav_candidate = not is_discursive and (
        is_strong_nav_marker or is_heading_like or is_editorial_heading
    )
    # Cabeçalho de seção real que ENTRA no corpo (preservável na fronteira).
    is_section_heading = is_editorial_heading or is_numbered_chapter

    return BlockSignals(
        text=text,
        word_count=wc,
        sentences=sentences,
        ends_sentence=ends_sentence,
        has_connective=has_connective,
        is_structural_label=is_structural_label,
        is_editorial_heading=is_editorial_heading,
        is_numbered_chapter=is_numbered_chapter,
        has_toc_leader=has_toc_leader,
        is_discursive=is_discursive,
        is_heading_like=is_heading_like,
        is_nav_candidate=is_nav_candidate,
        is_strong_nav_marker=is_strong_nav_marker,
        is_section_heading=is_section_heading,
    )


def classify_front_matter(
    blocks: list[str],
    *,
    min_run: int = MIN_NAV_RUN,
    front_zone: int = FRONT_ZONE_BLOCKS,
) -> dict[int, str]:
    """Classifica os blocos iniciais. Retorna {índice_do_bloco: rótulo}.

    Só rotula o que importa para a decisão de trim: navigation_noise (remover),
    e — na fronteira — editorial_discourse / main_body_start (preservar). Blocos
    não rotulados são corpo e ficam intactos.
    """
    n = min(len(blocks), front_zone)
    if n == 0:
        return {}
    sig = [signals(b) for b in blocks[:n]]
    labels: dict[int, str] = {}

    # R1 — rótulo estrutural isolado é sempre navegação (capa/sumário/landmarks).
    for i in range(n):
        if sig[i].is_structural_label:
            labels[i] = NAVIGATION_NOISE

    # R2 — primeira corrida de navegação QUALIFICADA: comprida o bastante e
    # com pelo menos um marcador estrutural forte (não só linhas curtas).
    selected = _first_qualified_run(sig, n, min_run)
    if selected is None:
        return labels

    start, end = selected
    frontier = end + 1  # primeiro bloco fora da corrida (a mudança de regime)
    keep_from = frontier  # por padrão remove [start..end] inteiro

    # Pullback de UM bloco na mudança de regime: quando a corrida desemboca em
    # prosa discursiva, o ÚLTIMO bloco costuma ser o cabeçalho que INAUGURA o
    # corpo (Capítulo I / Prefácio / o título da seção) — preserva-o. Vale só
    # se ele é cabeçalho de corpo (seção numerada/editorial OU cabeçalho-plano)
    # e NÃO um marcador inequívoco de TOC (líder pontilhado / rótulo
    # estrutural). Um único bloco mantém o pullback conservador: na pior das
    # hipóteses perde-se uma linha de título, nunca prosa.
    if frontier < n and sig[frontier].is_discursive:
        last = sig[end]
        end_opens_body = (
            (last.is_section_heading or last.is_heading_like)
            and not last.has_toc_leader
            and not last.is_structural_label
        )
        if end_opens_body:
            keep_from = end
            labels[end] = (
                EDITORIAL_DISCOURSE if last.is_editorial_heading else MAIN_BODY_START
            )

    for k in range(start, keep_from):
        labels[k] = NAVIGATION_NOISE
    if frontier < n and frontier not in labels:
        labels[frontier] = MAIN_BODY_START

    return labels


def _first_qualified_run(
    sig: list[BlockSignals], n: int, min_run: int
) -> tuple[int, int] | None:
    """Acha a 1ª corrida de nav_candidate com len>=min_run E marcador forte."""
    i = 0
    while i < n:
        if not sig[i].is_nav_candidate:
            i += 1
            continue
        j = i
        while j + 1 < n and sig[j + 1].is_nav_candidate:
            j += 1
        run_len = j - i + 1
        has_strong = any(sig[k].is_strong_nav_marker for k in range(i, j + 1))
        if run_len >= min_run and has_strong:
            return (i, j)
        i = j + 1
    return None


def front_matter_signature(
    *, min_run: int = MIN_NAV_RUN, front_zone: int = FRONT_ZONE_BLOCKS
) -> str:
    """Assinatura estável da configuração — entra em anchor_filling_hash.

    Muda o freeze_key quando a política de front matter muda, forçando re-bake
    controlado do catálogo (mesma disciplina do repertório de ruído).
    """
    return f"fm:{FRONT_MATTER_VERSION}:run={min_run}:zone={front_zone}"
