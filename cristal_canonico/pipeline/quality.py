#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quality.py - Layer 3: deterministic pre-embedding chunk quality checks.

Usage:
  python3 quality.py chunks.jsonl -o chunks.quality.jsonl --report quality_report.json
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone


FLAGS = [
    "too_short",
    "too_long",
    "many_short_lines",
    "many_numbers",
    "possible_bibliography",
    "possible_index",
    "possible_table",
    "ocr_noise",
    "repeated_text",
    "low_sentence_density",
    "many_references",
]
SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
REF_RE = re.compile(r"\b(?:doi|isbn|issn|http|www\.|op\. cit\.|ibid\.|et al\.)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}[a-z]?\b")


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc


def repeated_text_score(text):
    words = re.findall(r"\b\w+\b", text.lower())
    if len(words) < 80:
        return 0.0
    grams = [" ".join(words[i:i + 5]) for i in range(len(words) - 4)]
    if not grams:
        return 0.0
    counts = Counter(grams)
    repeated = sum(c - 1 for c in counts.values() if c > 1)
    return repeated / len(grams)


def flag_chunk(chunk):
    text = chunk.get("text", "")
    word_count = int(chunk.get("word_count") or len(text.split()))
    lines = [line for line in text.splitlines() if line.strip()]
    short_lines = [line for line in lines if len(line.strip()) <= 35]
    digit_chars = sum(1 for c in text if c.isdigit())
    alpha_chars = sum(1 for c in text if c.isalpha())
    sentence_count = len(SENTENCE_END_RE.findall(text))
    ref_hits = len(REF_RE.findall(text)) + len(YEAR_RE.findall(text))
    flags = []

    if word_count < 350:
        flags.append("too_short")
    if word_count > 1100:
        flags.append("too_long")
    if lines and len(short_lines) / len(lines) > 0.55 and len(lines) >= 8:
        flags.append("many_short_lines")
    if alpha_chars and digit_chars / max(1, alpha_chars + digit_chars) > 0.18:
        flags.append("many_numbers")
    if ref_hits >= 5 and ref_hits / max(1, word_count) > 0.015:
        flags.append("many_references")
    if ref_hits >= 6 and len(lines) >= 4:
        flags.append("possible_bibliography")
    if len(lines) >= 8 and sum(1 for l in lines if re.match(r"^\s*[A-ZÀ-ÖØ-Þ][^,]{1,40},\s+", l)) >= 3:
        flags.append("possible_bibliography")
    if len(lines) >= 8 and sum(1 for l in lines if re.search(r"\.{2,}\s*\d+\s*$", l)) >= 3:
        flags.append("possible_index")
    if len(lines) >= 5 and sum(1 for l in lines if re.search(r"\s{3,}|\t|\|", l)) / len(lines) > 0.35:
        flags.append("possible_table")
    weird = len(re.findall(r"[^\w\s.,;:!?()\[\]{}'\"/\\\-–—%$€£@#&*+=<>À-ÖØ-öø-ÿ]", text))
    if len(text) and weird / len(text) > 0.025:
        flags.append("ocr_noise")
    if repeated_text_score(text) > 0.08:
        flags.append("repeated_text")
    if word_count >= 150 and sentence_count / max(1, word_count / 100) < 1.2:
        flags.append("low_sentence_density")

    penalties = {
        "too_short": 0.18,
        "too_long": 0.16,
        "many_short_lines": 0.12,
        "many_numbers": 0.10,
        "possible_bibliography": 0.20,
        "possible_index": 0.18,
        "possible_table": 0.16,
        "ocr_noise": 0.18,
        "repeated_text": 0.18,
        "low_sentence_density": 0.12,
        "many_references": 0.12,
    }
    score = 1.0 - sum(penalties[f] for f in set(flags))
    score = max(0.0, min(1.0, score))
    status = "ok" if score >= 0.78 and not flags else "suspect"
    if score < 0.45:
        status = "bad"

    return {
        "status": status,
        "score": round(score, 4),
        "normalized_quality_score": round(score, 4),
        "flags": flags,
        "metrics": {
            "word_count": word_count,
            "line_count": len(lines),
            "short_line_ratio": round(len(short_lines) / len(lines), 4) if lines else 0.0,
            "digit_char_ratio": round(digit_chars / max(1, alpha_chars + digit_chars), 4),
            "sentence_count": sentence_count,
            "reference_hits": ref_hits,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="Layer 3: quality checks for chunks.")
    ap.add_argument("chunks", help="chunks.jsonl")
    ap.add_argument("-o", "--output", default="chunks.quality.jsonl")
    ap.add_argument("--report", default="quality_report.json")
    args = ap.parse_args()

    try:
        chunks = list(load_jsonl(args.chunks))
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))

    records = []
    flag_counts = Counter()
    status_counts = Counter()
    scores = []
    for chunk in chunks:
        quality = flag_chunk(chunk)
        scores.append(quality["score"])
        flag_counts.update(quality["flags"])
        status_counts[quality["status"]] += 1
        records.append({
            "chunk_id": chunk.get("chunk_id"),
            "book_id": chunk.get("book_id"),
            "chapter_id": chunk.get("chapter_id"),
            "quality": quality,
        })

    with open(args.output, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    report = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": args.chunks,
        "output_file": args.output,
        "total_chunks": len(records),
        "status_counts": dict(status_counts),
        "flag_counts": dict(flag_counts),
        "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "min_score": min(scores) if scores else 0.0,
        "max_score": max(scores) if scores else 0.0,
        "score_field_rule": "normalized_quality_score reuses quality.score from quality.py",
        "available_flags": FLAGS,
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Chunks evaluated: {report['total_chunks']} | avg score: {report['avg_score']}")
    print(f"Output: {args.output}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
