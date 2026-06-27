"""Diagnóstico de admissão de PDF (fase canônica, pré-trilhas).

Decide, ANTES das trilhas e ANTES do OCR pesado, se um PDF:

* ``pass``              — corpo principal suficiente e limpo; segue normal.
* ``pass_with_warning`` — há corpo útil, mas contaminado (front matter,
                          cabeçalho/rodapé corrente, sumário/expediente,
                          numeração solta, notas) ou com risco de OCR ruim.
* ``abort``             — não deveria seguir para trilha (sem camada textual
                          de corpo / confiança de corpo principal baixa).

Princípios (alinhados ao resto do projeto):

* Determinístico, stdlib-only, rápido, sem LLM. Cada sinal é um número
  auditável e cada veredito carrega a lista de razões que o produziram.
* Lê SÓ a camada textual (`pdftotext`), nunca dispara OCR. "Página sem texto"
  é o próprio sinal de scan/imagem — e abortar antes do OCR evita gastar OCR
  de centenas de páginas num livro que não entraria.
* Reusa o "mesmo cérebro" do front matter (`front_matter.signals`) para
  sumário/expediente — não reinventa o detector de regime.
* NÃO é o portão. O portão (`anchor/gate.py`) impõe o contrato C1–C7 sobre o
  corpo já extraído; este diagnóstico é um filtro de ADMISSÃO que roda antes,
  como a porta de tamanho. Mantém o portão puro e intacto.

A decisão de `classify.py` de não inferir nota por padrão de superfície é
respeitada aqui: `note_density` é sinal REPORTADO, nunca dispara `abort`.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pipeline import extract as _extract
from pipeline.front_matter import signals as _fm_signals

PDF_DIAGNOSTICS_VERSION = "0.1.0"

# --- parâmetros de amostragem -----------------------------------------------
ANALYZE_MAX_PAGES = 120   # teto de páginas lidas (1ª janela; barato e estável)
HEAD_PAGES = 6            # janela inicial p/ front matter / linhas curtas
MIN_PAGE_TEXT_CHARS = 100  # piso de letras p/ a página contar como "tem corpo"

# --- limiares de qualidade de OCR (calibrados em corpus real) ---------------
# Sub-sinais normalizados por estes divisores antes de combinar (ver ocr_risk).
OCR_REPL_PER_K = 1.0       # caracteres de substituição (U+FFFD) por mil chars
OCR_SINGLE_PCT = 1.0       # % de tokens de 1 letra "ilegítima" (fragmentação)
OCR_PUNCT_PCT = 0.5        # % de tokens com pontuação dentro da palavra

# --- limiares de decisão ----------------------------------------------------
ABORT_TEXT_RATIO = 0.15    # < deste % de páginas com texto ⇒ corpo inexistente
ABORT_BODY_CONF = 0.25     # confiança de corpo abaixo disto ⇒ abort
GOOD_DENSITY_CHARS = 800   # chars/página de texto p/ densidade "plena" (=1.0)

WARN_OCR_RISK = 0.30       # risco de OCR a partir do qual vira ressalva
WARN_HEADER_FOOTER = 0.50  # mesmo cabeçalho/rodapé na maioria das páginas
WARN_LOOSE_NUMBER = 0.18   # fração de linhas que são só número (paginação solta)
WARN_SHORT_LINE = 0.55     # fração de linhas curtas no começo (fragmentação)
WARN_BODY_CONF = 0.55      # confiança de corpo morna ⇒ ressalva (não abort)

# --- regex determinísticos (concern próprio; não acopla a privados) ---------
_LETTER = r"[^\W\d_]"      # letra unicode (sem dígito/underscore)
_LETTER_RE = re.compile(_LETTER)
# Pontuação "no meio da palavra" típica de OCR ruim: EMO<;OES, Introdur;ao,
# Pref:icio — letra, lixo, letra/dígito.
_PUNCT_IN_WORD_RE = re.compile(_LETTER + r"[<>;:#~^/\\|]" + r"[\w]")
# Linha que é só número (paginação solta), com travessões/espaços opcionais.
_LOOSE_NUMBER_RE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")
# Entrada numerada tipo nota/lista ("12. texto", "3) texto").
_NUMBERED_ENTRY_RE = re.compile(r"^\s*\d{1,3}[.)]\s+" + _LETTER)
# Marcadores de expediente/colofão (catalogação) — quase nunca em prosa autoral.
_EXPEDIENTE_RE = re.compile(
    r"\bISBN\b|\bCDD\b|\bCDU\b|Cataloga|Câmara Brasileira do Livro|Copyright ©",
    re.IGNORECASE,
)
# Letras isoladas legítimas (artigos/iniciais comuns em pt/en) — não contam
# como fragmentação de OCR.
_LEGIT_SINGLE = set("aAeEoOiIuUyYàáâãéêíóôõúÀÁÉÍÓÚ")


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


@dataclass(frozen=True)
class Reason:
    """Uma razão auditável por trás do veredito."""

    code: str
    severity: str   # "abort" | "warn"
    message: str
    value: float | None = None


@dataclass(frozen=True)
class PdfDiagnostics:
    """Sinais + veredito de admissão de um PDF (serializável p/ JSON)."""

    version: str
    decision: str                 # "pass" | "pass_with_warning" | "abort"
    body_confidence: float
    ocr_risk: float
    # cobertura / corpo
    total_pages: int
    pages_analyzed: int
    pages_with_text: int
    text_page_ratio: float
    mean_chars_per_text_page: float
    # contaminação
    short_line_ratio_head: float
    repeated_header_footer_ratio: float
    front_matter_at_head: bool
    structural_label_count: int
    toc_leader_count: int
    expediente_marker_count: int
    loose_numbering_ratio: float
    note_density: float
    # sub-sinais de OCR (transparência)
    ocr_replacement_per_k: float
    ocr_single_letter_pct: float
    ocr_punct_in_word_pct: float
    reasons: list[Reason] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reasons"] = [asdict(r) for r in self.reasons]
        return d


def _nonempty_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _ocr_subsignals(text: str) -> tuple[float, float, float]:
    """(repl_por_mil, % token 1-letra ilegítima, % token c/ pontuação interna)."""
    n_chars = max(1, len(text))
    repl_per_k = text.count("�") / n_chars * 1000.0

    tokens = text.split()
    n_tok = max(1, len(tokens))
    single = sum(
        1 for t in tokens if len(t) == 1 and t.isalpha() and t not in _LEGIT_SINGLE
    )
    punct = sum(1 for t in tokens if _PUNCT_IN_WORD_RE.search(t))
    return (
        repl_per_k,
        single / n_tok * 100.0,
        punct / n_tok * 100.0,
    )


def _ocr_risk(repl_per_k: float, single_pct: float, punct_pct: float) -> float:
    """Combina os três sub-sinais de OCR em um risco 0..1.

    Pesos: substituição (sinal mais forte de glifo quebrado) > fragmentação de
    palavra > pontuação no meio da palavra. Cada componente é saturado em 1.
    """
    repl_c = _clamp01(repl_per_k / OCR_REPL_PER_K)
    single_c = _clamp01(single_pct / OCR_SINGLE_PCT)
    punct_c = _clamp01(punct_pct / OCR_PUNCT_PCT)
    return _clamp01(0.5 * repl_c + 0.3 * single_c + 0.2 * punct_c)


def _header_footer_ratio(text_pages: list[str]) -> float:
    """Fração de páginas que compartilham o MESMO cabeçalho OU rodapé.

    Cabeçalho/rodapé corrente vaza para o corpo; sua repetição é o sinal. A
    primeira/última linha de cada página é normalizada (minúscula, sem dígitos
    nem pontuação) e contada — o pico relativo mede a corrida de repetição.
    """
    if len(text_pages) < 3:
        return 0.0

    def _norm(line: str) -> str:
        line = re.sub(r"[\d\W_]+", " ", line.lower())
        return " ".join(line.split())

    heads: dict[str, int] = {}
    foots: dict[str, int] = {}
    counted = 0
    for page in text_pages:
        lines = _nonempty_lines(page)
        if not lines:
            continue
        counted += 1
        h, f = _norm(lines[0]), _norm(lines[-1])
        if len(h) >= 4:
            heads[h] = heads.get(h, 0) + 1
        if len(f) >= 4:
            foots[f] = foots.get(f, 0) + 1
    if counted < 3:
        return 0.0
    top = max(
        [c for c in heads.values()] + [c for c in foots.values()] + [0]
    )
    return top / counted


def diagnose_pages(pages: list[str], *, total_pages: int | None = None) -> PdfDiagnostics:
    """Núcleo PURO: lista de textos-por-página → diagnóstico (sem subprocess).

    `pages` é a camada textual página a página (string vazia = página sem
    texto). `total_pages` é o total real do PDF (default: páginas analisadas).
    """
    pages_analyzed = len(pages)
    total = total_pages if total_pages is not None else pages_analyzed

    text_pages = [p for p in pages if len(_LETTER_RE.findall(p)) >= MIN_PAGE_TEXT_CHARS]
    pages_with_text = len(text_pages)
    text_page_ratio = pages_with_text / pages_analyzed if pages_analyzed else 0.0
    mean_chars = (
        sum(len(p) for p in text_pages) / pages_with_text if pages_with_text else 0.0
    )

    # --- OCR sobre o texto efetivamente presente ---------------------------
    sample = "\n".join(text_pages) if text_pages else "\n".join(pages)
    repl_per_k, single_pct, punct_pct = _ocr_subsignals(sample)
    ocr_risk = _ocr_risk(repl_per_k, single_pct, punct_pct)

    # --- confiança de corpo principal --------------------------------------
    density_factor = _clamp01(mean_chars / GOOD_DENSITY_CHARS)
    body_confidence = _clamp01(text_page_ratio * density_factor * (1 - 0.5 * ocr_risk))

    # --- contaminação no começo (front matter / linhas curtas) -------------
    head_text = "\n\n".join(pages[:HEAD_PAGES])
    head_lines = _nonempty_lines(head_text)
    short_head = sum(1 for ln in head_lines if len(ln) < 25)
    short_line_ratio_head = short_head / len(head_lines) if head_lines else 0.0

    structural = toc_leaders = expediente = 0
    for ln in head_lines:
        sig = _fm_signals(ln)
        if sig.is_structural_label:
            structural += 1
        if sig.has_toc_leader:
            toc_leaders += 1
        if _EXPEDIENTE_RE.search(ln):
            expediente += 1
    front_matter_at_head = (structural + toc_leaders + expediente) >= 3

    # --- numeração solta + densidade de notas (documento amostrado) --------
    all_lines = _nonempty_lines("\n".join(pages))
    n_lines = max(1, len(all_lines))
    loose_numbering_ratio = (
        sum(1 for ln in all_lines if _LOOSE_NUMBER_RE.match(ln)) / n_lines
    )
    note_density = (
        sum(1 for ln in all_lines if _NUMBERED_ENTRY_RE.match(ln)) / n_lines
    )

    header_footer_ratio = _header_footer_ratio(text_pages)

    # --- política de decisão (razões auditáveis) ---------------------------
    reasons: list[Reason] = []

    if pages_with_text == 0:
        reasons.append(Reason(
            "no_text_layer", "abort",
            "nenhuma página com camada textual de corpo (scan/só-imagem ou "
            "PDF que exige OCR pesado)", text_page_ratio,
        ))
    else:
        if text_page_ratio < ABORT_TEXT_RATIO:
            reasons.append(Reason(
                "sparse_text_layer", "abort",
                f"só {text_page_ratio:.0%} das páginas têm corpo textual "
                f"(< {ABORT_TEXT_RATIO:.0%})", text_page_ratio,
            ))
        if body_confidence < ABORT_BODY_CONF:
            reasons.append(Reason(
                "low_body_confidence", "abort",
                f"confiança de corpo principal {body_confidence:.2f} "
                f"< {ABORT_BODY_CONF:.2f}", body_confidence,
            ))

    if ocr_risk >= WARN_OCR_RISK:
        reasons.append(Reason(
            "ocr_quality_risk", "warn",
            f"risco de OCR ruim {ocr_risk:.2f} (subst={repl_per_k:.2f}/k, "
            f"1-letra={single_pct:.2f}%, pont.intra={punct_pct:.2f}%)", ocr_risk,
        ))
    if WARN_BODY_CONF > body_confidence >= ABORT_BODY_CONF:
        reasons.append(Reason(
            "moderate_body_confidence", "warn",
            f"confiança de corpo morna {body_confidence:.2f} "
            f"(< {WARN_BODY_CONF:.2f})", body_confidence,
        ))
    if front_matter_at_head:
        reasons.append(Reason(
            "front_matter_at_head", "warn",
            f"sumário/expediente no começo (rótulos={structural}, "
            f"linhas-TOC={toc_leaders}, colofão={expediente})", None,
        ))
    if header_footer_ratio >= WARN_HEADER_FOOTER:
        reasons.append(Reason(
            "running_header_footer", "warn",
            f"cabeçalho/rodapé repetido em {header_footer_ratio:.0%} das "
            "páginas (vaza para o corpo)", header_footer_ratio,
        ))
    if loose_numbering_ratio >= WARN_LOOSE_NUMBER:
        reasons.append(Reason(
            "loose_numbering", "warn",
            f"numeração solta em {loose_numbering_ratio:.0%} das linhas",
            loose_numbering_ratio,
        ))
    if short_line_ratio_head >= WARN_SHORT_LINE and body_confidence < WARN_BODY_CONF:
        reasons.append(Reason(
            "fragmented_head", "warn",
            f"{short_line_ratio_head:.0%} de linhas curtas no começo "
            "(fragmentação/capa/índice)", short_line_ratio_head,
        ))

    if any(r.severity == "abort" for r in reasons):
        decision = "abort"
    elif any(r.severity == "warn" for r in reasons):
        decision = "pass_with_warning"
    else:
        decision = "pass"

    return PdfDiagnostics(
        version=PDF_DIAGNOSTICS_VERSION,
        decision=decision,
        body_confidence=round(body_confidence, 4),
        ocr_risk=round(ocr_risk, 4),
        total_pages=total,
        pages_analyzed=pages_analyzed,
        pages_with_text=pages_with_text,
        text_page_ratio=round(text_page_ratio, 4),
        mean_chars_per_text_page=round(mean_chars, 1),
        short_line_ratio_head=round(short_line_ratio_head, 4),
        repeated_header_footer_ratio=round(header_footer_ratio, 4),
        front_matter_at_head=front_matter_at_head,
        structural_label_count=structural,
        toc_leader_count=toc_leaders,
        expediente_marker_count=expediente,
        loose_numbering_ratio=round(loose_numbering_ratio, 4),
        note_density=round(note_density, 4),
        ocr_replacement_per_k=round(repl_per_k, 4),
        ocr_single_letter_pct=round(single_pct, 4),
        ocr_punct_in_word_pct=round(punct_pct, 4),
        reasons=reasons,
    )


def diagnose_pdf(
    path: str | Path, *, max_pages: int | None = ANALYZE_MAX_PAGES
) -> PdfDiagnostics:
    """Caminho de produção: lê a camada textual do PDF e diagnostica.

    Nunca dispara OCR. Levanta as exceções de `extract` (binário ausente etc.)
    para o caller decidir — o `run` trata como "diagnóstico indisponível" e
    segue o fluxo, sem quebrar o pipeline.
    """
    pages = _extract.pdf_pages_text_layer(path, max_pages=max_pages)
    try:
        total = _extract.pdf_page_count(path)
    except Exception:
        total = len(pages)
    return diagnose_pages(pages, total_pages=total)
