"""extract — formato → texto bruto (spec 4.1).

Dois caminhos:

* Estruturado (EPUB/HTML): emite sentinelas de bloco a partir da marcação
  (`<blockquote>` → ⟦PRL:BQ⟧…⟦/PRL:BQ⟧, nota → ⟦PRL:FN⟧…), cada um isolado
  em linha; blocos juntados por "\\n\\n". NOTEREF inline para anchors de nota.
* Texto plano (PDF/TXT): lê texto cru, NÃO emite sentinela (não há estrutura
  a montante). PDF usa `pdftotext` quando houver camada textual; se for scan
  só-imagem, faz OCR com `pdftoppm + tesseract`.

Saída: a string de texto bruto. O caller persiste como `entrada.raw.txt` e
calcula `raw_hash = sha256_text(raw_text)`.

Stdlib-only no código Python: HTML via html.parser, EPUB via zipfile +
xml.etree. O caminho de PDF usa binários externos (`pdffonts`, `pdftotext`,
`pdftoppm`, `tesseract`) para manter o pipeline leve e determinístico.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from core.sentinels import (
    BQ_CLOSE,
    BQ_OPEN,
    FN_CLOSE,
    FN_OPEN,
    NOTEREF_CLOSE,
    NOTEREF_OPEN,
)

_NOTEREF_CLASS_HINTS = ("noteref", "footnote-ref", "fnref", "noteanchor")
_FOOTNOTE_TYPE_HINTS = ("footnote", "rearnote", "endnote", "note")
_FOOTNOTE_CLASS_HINTS = ("footnote", "endnote")
_FOOTNOTE_TAGS = {"aside", "div", "li", "section", "p"}
_BLOCK_BREAK = {
    "p", "div", "li", "tr", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption",
}
_SKIP_TAGS = {"script", "style", "head", "title", "meta", "link"}
_PDF_MIN_TEXT_CHARS = 80
_OCR_DEFAULT_DPI = 300
_OCR_DEFAULT_LANG = "por"
_OCR_DEFAULT_PSM = 1


class _HTMLSentinelParser(HTMLParser):
    """Converte marcação em texto com sentinelas isolados em linha."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        # pilha de (tag, kind) — kind em {'skip','noteref','bq','fn','block',None}
        self._stack: list[tuple[str, str | None]] = []

    def _flush_para(self) -> None:
        text = " ".join("".join(self._buf).split())
        if text:
            self.blocks.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):  # noqa: D102
        if tag in _SKIP_TAGS:
            self._skip += 1
            self._stack.append((tag, "skip"))
            return
        a = dict(attrs)
        et = (a.get("epub:type") or a.get("type") or "").lower()
        cls = (a.get("class") or "").lower()

        if tag in ("a", "sup", "span") and (
            "noteref" in et or any(h in cls for h in _NOTEREF_CLASS_HINTS)
        ):
            self._buf.append(NOTEREF_OPEN)
            self._stack.append((tag, "noteref"))
            return

        if tag == "blockquote":
            self._flush_para()
            self.blocks.append(BQ_OPEN)
            self._stack.append((tag, "bq"))
            return

        is_fn = any(h in et for h in _FOOTNOTE_TYPE_HINTS) or any(
            h in cls for h in _FOOTNOTE_CLASS_HINTS
        )
        if is_fn and tag in _FOOTNOTE_TAGS:
            self._flush_para()
            self.blocks.append(FN_OPEN)
            self._stack.append((tag, "fn"))
            return

        if tag in _BLOCK_BREAK:
            self._flush_para()
            self._stack.append((tag, "block"))
            return

        self._stack.append((tag, None))

    def handle_startendtag(self, tag, attrs):  # noqa: D102
        if tag == "br" and not self._skip:
            self._buf.append(" ")

    def handle_endtag(self, tag):  # noqa: D102
        kind = None
        # desempilha até casar a tag (tolera marcação desbalanceada)
        while self._stack:
            t, k = self._stack.pop()
            if t == tag:
                kind = k
                break
        if kind == "skip":
            self._skip = max(0, self._skip - 1)
        elif kind == "noteref":
            self._buf.append(NOTEREF_CLOSE)
        elif kind == "bq":
            self._flush_para()
            self.blocks.append(BQ_CLOSE)
        elif kind == "fn":
            self._flush_para()
            self.blocks.append(FN_CLOSE)
        elif kind == "block":
            self._flush_para()

    def handle_data(self, data):  # noqa: D102
        if not self._skip:
            self._buf.append(data)

    def result(self) -> str:
        self._flush_para()
        return "\n\n".join(self.blocks)


def extract_html(html_text: str) -> str:
    """HTML/XHTML → texto bruto com sentinelas."""
    parser = _HTMLSentinelParser()
    parser.feed(html_text)
    parser.close()
    return parser.result()


def extract_txt(path: Path) -> str:
    """TXT → texto cru, sem sentinelas (caminho plano)."""
    return Path(path).read_text(encoding="utf-8")


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"binário obrigatório ausente para PDF: {name}")
    return path


def _run_text(cmd: list[str], *, stdin: bytes | None = None) -> str:
    proc = subprocess.run(cmd, input=stdin, capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"comando falhou ({cmd[0]}): {stderr or f'rc={proc.returncode}'}"
        )
    return proc.stdout.decode("utf-8", "replace")


def _normalize_plain_text(text: str) -> str:
    """Normaliza saída plana de PDF/TXT sem introduzir estrutura sintética."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\f", "\n\n")
    return text.strip()


def _pdf_extract_via_pdftotext(path: Path) -> str:
    _require_binary("pdftotext")
    return _normalize_plain_text(
        _run_text(["pdftotext", "-layout", str(path), "-"])
    )


def _pdf_has_meaningful_text(text: str) -> bool:
    return len(text.replace("\n", " ").strip()) >= _PDF_MIN_TEXT_CHARS


def _pdf_has_text_layer(path: Path) -> bool:
    _require_binary("pdffonts")
    proc = subprocess.run(
        ["pdffonts", str(path)], capture_output=True, text=True
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(
            f"comando falhou (pdffonts): {stderr or f'rc={proc.returncode}'}"
        )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    # `pdffonts` imprime cabeçalho + linha tracejada; linhas extras indicam fontes.
    return len(lines) > 2


def _ocr_pdf_page(path: Path, page: int) -> str:
    _require_binary("pdftoppm")
    _require_binary("tesseract")
    with subprocess.Popen(
        [
            "pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-r",
            str(_OCR_DEFAULT_DPI),
            "-gray",
            str(path),
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as render:
        ppm_bytes, render_err = render.communicate()
    if not ppm_bytes:
        err = render_err.decode("utf-8", "replace").strip()
        raise RuntimeError(f"pdftoppm não gerou imagem para página {page}: {err}")
    return _run_text(
        [
            "tesseract",
            "stdin",
            "stdout",
            "--psm",
            str(_OCR_DEFAULT_PSM),
            "-l",
            _OCR_DEFAULT_LANG,
        ],
        stdin=ppm_bytes,
    ).strip()


def _pdf_page_count(path: Path) -> int:
    _require_binary("pdfinfo")
    proc = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(
            f"comando falhou (pdfinfo): {stderr or f'rc={proc.returncode}'}"
        )
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise RuntimeError("pdfinfo não informou número de páginas")


def _pdf_extract_via_ocr(path: Path) -> str:
    pages = _pdf_page_count(path)
    texts = [_ocr_pdf_page(path, page) for page in range(1, pages + 1)]
    return _normalize_plain_text("\n\n".join(texts))


def pdf_page_count(path: Path) -> int:
    """Número de páginas do PDF (wrapper público de `pdfinfo`)."""
    return _pdf_page_count(path)


def pdf_pages_text_layer(
    path: str | Path, *, max_pages: int | None = None
) -> list[str]:
    """Texto da CAMADA TEXTUAL do PDF, uma string por página (sem OCR).

    Usado pelo diagnóstico de admissão (`pdf_diagnostics`): NUNCA dispara OCR
    (`pdftoppm`/`tesseract`), só lê a camada textual via `pdftotext` — barato e
    determinístico. Uma página sem texto vem como string vazia/curta, e ESSE é
    o sinal de scan/imagem (corpo só-imagem ⇒ baixa confiança de corpo).

    Diferente de `_pdf_extract_via_pdftotext`, preserva a fronteira de página
    (`\\f`) em vez de colapsá-la — o diagnóstico precisa raciocinar por página
    (cabeçalho/rodapé corrente, cobertura de texto).
    """
    _require_binary("pdftotext")
    cmd = ["pdftotext", "-layout"]
    if max_pages is not None:
        cmd += ["-l", str(int(max_pages))]
    cmd += [str(Path(path)), "-"]
    out = _run_text(cmd)
    pages = out.split("\f")
    if pages and pages[-1] == "":
        pages.pop()  # artefato do "\f" terminal do pdftotext
    return pages


def extract_pdf(path: Path) -> str:
    """PDF → texto cru; usa camada textual quando houver, senão OCR."""
    if _pdf_has_text_layer(path):
        text = _pdf_extract_via_pdftotext(path)
        if _pdf_has_meaningful_text(text):
            return text
    text = _pdf_extract_via_ocr(path)
    if not text:
        raise RuntimeError("OCR do PDF não produziu texto")
    return text


# --- EPUB (zip + OPF) -------------------------------------------------------

_NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
}


def _epub_spine_hrefs(zf: zipfile.ZipFile) -> list[str]:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(".//container:rootfile", _NS)
    if rootfile is None:
        return []
    opf_path = rootfile.attrib["full-path"]
    opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
    opf = ET.fromstring(zf.read(opf_path))
    manifest = {
        item.attrib["id"]: item.attrib["href"]
        for item in opf.findall(".//opf:manifest/opf:item", _NS)
    }
    hrefs: list[str] = []
    for ref in opf.findall(".//opf:spine/opf:itemref", _NS):
        href = manifest.get(ref.attrib.get("idref", ""))
        if href:
            hrefs.append(f"{opf_dir}/{href}" if opf_dir else href)
    return hrefs


def extract_epub(path: Path) -> str:
    """EPUB → texto bruto com sentinelas, na ordem da spine."""
    parts: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        hrefs = _epub_spine_hrefs(zf) or sorted(
            n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))
        )
        for href in hrefs:
            if href in names:
                html_text = zf.read(href).decode("utf-8", errors="replace")
                doc = extract_html(html_text)
                if doc:
                    parts.append(doc)
    return "\n\n".join(parts)


def extract(path: str | Path) -> str:
    """Despacha por extensão; devolve o texto bruto."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".html", ".htm", ".xhtml"):
        return extract_html(p.read_text(encoding="utf-8"))
    if suffix == ".epub":
        return extract_epub(p)
    if suffix in (".txt", ".text", ""):
        return extract_txt(p)
    if suffix == ".pdf":
        return extract_pdf(p)
    raise ValueError(f"Formato não suportado: {suffix!r}")
