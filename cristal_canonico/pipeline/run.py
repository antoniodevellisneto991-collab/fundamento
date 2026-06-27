"""Driver do pipeline: formato → corpo → estrutura → chunk, via portão.

extract → dispatch (preclean canônico + portão) → segment → chunk.
Escreve artefatos rastreáveis: entrada.raw.txt, book.body.txt,
structure.json, chunks.jsonl, e registra no catálogo (anchor_catalog.jsonl).

O portão é a única porta de saída válida: ou book.body.txt (passou) ou
abort (falhou) — sem terceiro caminho.

PRÉ-CONDIÇÃO (novo): diagnóstico de PDF antes do extract, com memória de
resolução. Permite abortar caro, reconhecer padrões aprendidos.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from anchor.catalog import AnchorCatalog
from anchor.contract import CONTRACT_VERSION, AnchorCtx
from anchor.dispatch import NeedsTypedHook, TooSmallError, dispatch
from core.text import DEFAULT_MIN_SIZE, ENGINE_VERSION, sha256_text
from pipeline.chunk import FORMAL_MAX_CHARS, FORMAL_TARGET_CHARS, chunk
from pipeline.quality import flag_chunk
from pipeline.classify import (
    class_counts,
    classification_records,
    classify_structure,
)
from pipeline.extract import extract
from pipeline.front_matter import front_matter_signature
from pipeline.noise import CANONICAL_NOISE_PATTERNS, repertoire_hash
from pipeline.segment import segment
from pipeline.pdf_diagnostics import diagnose_pdf as _diagnose_pdf
from pipeline.pdf_resolution_memory import seed_default_patterns

# Formatos com estrutura de bloco confiável (extract emite parágrafos isolados):
# só neles o trim de front matter é seguro/ligado por padrão.
_STRUCTURED_FORMATS = {"epub", "html"}

_FORMAT_BY_SUFFIX = {
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".epub": "epub", ".pdf": "pdf", ".txt": "txt",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pipeline de canonicalização de texto")
    p.add_argument("input", help="arquivo de entrada (epub/html/txt/pdf)")
    p.add_argument("-o", "--out", default="out", help="diretório de saída")
    p.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE)
    p.add_argument("--size-unit", default="words", choices=["words", "chars"])
    p.add_argument("--source-format", default=None)
    p.add_argument("--source-edition", default="unknown")
    p.add_argument("--translation-layer", default=None)
    p.add_argument(
        "--noise-pattern",
        action="append",
        default=[],
        dest="extra_noise_patterns",
        metavar="REGEX",
        help="adiciona padrão de ruído extra (repetível; acumula sobre o repertório canônico)",
    )
    p.add_argument(
        "--keep-front-matter",
        action="store_true",
        help="desliga o trim de navegação/embalagem do começo (mantém capa/sumário)",
    )
    p.add_argument(
        "--skip-pdf-diagnosis",
        action="store_true",
        help="desliga o diagnóstico de admissão de PDF (só PDF; pula verificação)",
    )
    p.add_argument(
        "--force-process",
        action="store_true",
        help="força processamento mesmo que diagnóstico recomende abort",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    in_path = Path(args.input)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # PRÉ-CONDIÇÃO: diagnóstico de PDF (se aplicável)
    pdf_diag = None
    matched_pattern = None
    if in_path.suffix.lower() == ".pdf" and not args.skip_pdf_diagnosis:
        try:
            pdf_diag = _diagnose_pdf(in_path)
            (out / "pdf_diagnostics.json").write_text(
                json.dumps(pdf_diag.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[diagnóstico PDF] decisão={pdf_diag.decision} "
                  f"confiança={pdf_diag.body_confidence:.2f} "
                  f"risco_ocr={pdf_diag.ocr_risk:.2f}", file=sys.stderr)

            # Consultar memória de resolução
            memory = seed_default_patterns()
            memory_path = out / "pdf_resolution_memory.json"
            if memory_path.exists():
                from pipeline.pdf_resolution_memory import ResolutionMemory
                memory = ResolutionMemory.load(memory_path)

            matches = memory.find_matching_patterns(pdf_diag)
            if matches:
                matched_pattern, conf = matches[0]
                print(f"[memória] padrão reconhecido: {matched_pattern.id} "
                      f"(confiança={conf:.2f})", file=sys.stderr)

            # Decidir sobre abort/warn antes de extract
            if pdf_diag.decision == "abort" and not args.force_process:
                print(f"ABORT (diagnóstico PDF): {pdf_diag.reasons[0].message if pdf_diag.reasons else 'sem corpo válido'}",
                      file=sys.stderr)
                return 4  # exit code específico para diagnóstico
            if pdf_diag.decision == "pass_with_warning":
                print(f"[aviso] {'; '.join(r.message for r in pdf_diag.reasons if r.severity == 'warn')}",
                      file=sys.stderr)
        except Exception as e:
            print(f"[aviso] diagnóstico de PDF falhou: {e} (continuando sem diagnóstico)",
                  file=sys.stderr)

    raw = extract(in_path)
    (out / "entrada.raw.txt").write_text(raw, encoding="utf-8")
    raw_hash = sha256_text(raw)

    source_format = args.source_format or _FORMAT_BY_SUFFIX.get(
        in_path.suffix.lower(), "txt"
    )
    noise_patterns = CANONICAL_NOISE_PATTERNS + tuple(args.extra_noise_patterns)
    trim_front_matter = (
        source_format in _STRUCTURED_FORMATS and not args.keep_front_matter
    )
    filling_hash = repertoire_hash(noise_patterns)
    if trim_front_matter:
        # Front matter muda o corpo -> entra no freeze_key (re-bake controlado).
        filling_hash = sha256_text(filling_hash + "\x1f" + front_matter_signature())
    ctx = AnchorCtx(
        raw_hash=raw_hash,
        anchor_filling_hash=filling_hash,
        engine_version=ENGINE_VERSION,
        contract_version=CONTRACT_VERSION,
        source_format=source_format,
        source_edition=args.source_edition,
        translation_layer=args.translation_layer,
        min_size=args.min_size,
        size_unit=args.size_unit,
        residual_noise_patterns=noise_patterns,
        trim_front_matter=trim_front_matter,
    )

    catalog = AnchorCatalog(out / "anchor_catalog.jsonl")
    try:
        body = dispatch(raw, ctx, catalog)
    except TooSmallError as e:
        print(f"ABORT (porta de tamanho): {e}", file=sys.stderr)
        return 2
    except NeedsTypedHook as e:
        print(f"ABORT (Degrau 4 — hook humano): {e}", file=sys.stderr)
        print(json.dumps(_diag(e.diagnostics), ensure_ascii=False, indent=2),
              file=sys.stderr)
        return 3

    structure = segment(body, ctx)
    # Classificação estrutural de blocos (pré-trilhas): rotula cada bloco nas
    # cinco classes mínimas. Determinística; não altera segment/chunk.
    classes = classify_structure(structure)
    records = classification_records(structure, classes, body)
    chunks = chunk(structure, ctx)

    # Chunks formais: janelas maiores (~350 palavras) para estilometria e
    # curadoria de voz. Mesma estrutura, parâmetros diferentes — e SÓ a voz do
    # autor: citação (blockquote) e nota (footnote) ficam fora antes de janelar,
    # senão a assinatura estilométrica mistura voz alheia (medido no Goffman:
    # ~45% das janelas formais ficariam contaminadas sem o filtro).
    import types as _types
    _formal_ctx = _types.SimpleNamespace(
        chunk_target_chars=FORMAL_TARGET_CHARS,
        chunk_max_chars=FORMAL_MAX_CHARS,
    )
    formal_chunks = chunk(structure, _formal_ctx, keep_block_types={"body"})

    (out / "book.body.txt").write_text(body, encoding="utf-8")
    (out / "structure.json").write_text(
        json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (out / "classification.jsonl").open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")
    with (out / "chunks.formal.jsonl").open("w", encoding="utf-8") as f:
        for ch in formal_chunks:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")

    quality_records = []
    for ch in chunks:
        q = flag_chunk(ch)
        quality_records.append({
            "chunk_id": ch["chunk_id"],
            "chapter_id": ch.get("chapter_id"),
            "quality": q,
        })
    with (out / "chunks.quality.jsonl").open("w", encoding="utf-8") as f:
        for rec in quality_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    formal_quality_records = []
    for ch in formal_chunks:
        q = flag_chunk(ch)
        formal_quality_records.append({
            "chunk_id": ch["chunk_id"],
            "chapter_id": ch.get("chapter_id"),
            "quality": q,
        })
    with (out / "chunks.formal.quality.jsonl").open("w", encoding="utf-8") as f:
        for rec in formal_quality_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Registrar caso resolvido na memória de resolução (se PDF)
    if pdf_diag is not None:
        from pipeline.pdf_resolution_memory import ResolutionMemory
        memory_path = out / "pdf_resolution_memory.json"
        # Carregar memória existente ou criar nova com padrões iniciais
        if memory_path.exists():
            memory = ResolutionMemory.load(memory_path)
        else:
            memory = seed_default_patterns()
        strategy_id = matched_pattern.id if matched_pattern else None
        memory.register_case(
            str(in_path),
            pdf_diag,
            decision="pass",  # chegou aqui = passou
            strategy_id=strategy_id,
            resolution_method="pattern_match" if matched_pattern else "default",
            notes=f"Processado com sucesso. Padrão: {strategy_id or 'nenhum'}",
        )
        memory.save(memory_path)

    ok_chunks = sum(1 for r in quality_records if r["quality"]["status"] == "ok")
    ok_formal = sum(1 for r in formal_quality_records if r["quality"]["status"] == "ok")
    print(f"OK · body_hash={sha256_text(body)[:12]} · "
          f"paragraphs={len(structure['paragraphs'])} · chunks={len(chunks)} · "
          f"chunks.formal={len(formal_chunks)} · "
          f"quality_ok={ok_chunks}/{len(chunks)} · "
          f"formal_quality_ok={ok_formal}/{len(formal_chunks)} · "
          f"classes={class_counts(classes)} · catalog={catalog.stats()}")
    return 0


def _diag(d: dict) -> dict:
    """Torna os GateCheck serializáveis para o stderr."""
    out = {}
    for k, v in d.items():
        out[k] = None if v is None else {"code": v.code, "message": v.message}
    return out


if __name__ == "__main__":
    raise SystemExit(main())
