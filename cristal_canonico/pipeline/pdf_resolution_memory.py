"""Memória de resolução para padrões aprendidos de PDF difíceis.

Quando um PDF é resolvido (mesmo que de forma manual ou especial), o sistema
registra COMO foi resolvido, acumulando conhecimento para reutilização.

Permite que casos similares no futuro reconheçam padrões já aprendidos e
reaplicam a mesma estratégia, em vez de começar do zero.

Componentes:

* ResolutionPattern: um padrão aprendido (critérios + estratégia + histórico).
* ResolutionMemory: registry de padrões + log de casos resolvidos.
* find_matching_patterns(): busca padrões que casam com diagnóstico atual.
* register_case(): registra novo caso resolvido para futuro aprendizado.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.text import sha256_text
from pipeline.pdf_diagnostics import PdfDiagnostics

PDF_RESOLUTION_MEMORY_VERSION = "0.1.0"


@dataclass
class MatchingCriteria:
    """Critérios que um diagnóstico deve satisfazer para casar com o padrão.

    Todos os campos presentes devem ser verdadeiros (AND lógico). None significa
    "não importa" (critério ignorado).
    """

    text_page_ratio_lt: float | None = None
    text_page_ratio_ge: float | None = None
    body_confidence_lt: float | None = None
    body_confidence_ge: float | None = None
    ocr_risk_ge: float | None = None
    ocr_risk_lt: float | None = None
    front_matter_at_head: bool | None = None
    header_footer_ratio_ge: float | None = None
    short_line_ratio_head_ge: float | None = None

    def matches(self, diag: PdfDiagnostics) -> bool:
        """True se o diagnóstico satisfaz todos os critérios não-None."""
        if self.text_page_ratio_lt is not None:
            if diag.text_page_ratio >= self.text_page_ratio_lt:
                return False
        if self.text_page_ratio_ge is not None:
            if diag.text_page_ratio < self.text_page_ratio_ge:
                return False
        if self.body_confidence_lt is not None:
            if diag.body_confidence >= self.body_confidence_lt:
                return False
        if self.body_confidence_ge is not None:
            if diag.body_confidence < self.body_confidence_ge:
                return False
        if self.ocr_risk_ge is not None:
            if diag.ocr_risk < self.ocr_risk_ge:
                return False
        if self.ocr_risk_lt is not None:
            if diag.ocr_risk >= self.ocr_risk_lt:
                return False
        if self.front_matter_at_head is not None:
            if diag.front_matter_at_head != self.front_matter_at_head:
                return False
        if self.header_footer_ratio_ge is not None:
            if diag.repeated_header_footer_ratio < self.header_footer_ratio_ge:
                return False
        if self.short_line_ratio_head_ge is not None:
            if diag.short_line_ratio_head < self.short_line_ratio_head_ge:
                return False
        return True

    def confidence_score(self, diag: PdfDiagnostics) -> float:
        """0..1: como bem o diagnóstico casa com este padrão.

        Baseado em proximidade aos limiares — quanto mais perto, mais segura a
        correspondência.
        """
        if not self.matches(diag):
            return 0.0
        score = 1.0
        if self.text_page_ratio_lt is not None:
            gap = self.text_page_ratio_lt - diag.text_page_ratio
            if gap > 0.2:
                score *= 0.8  # margem confortável
            elif gap < 0.05:
                score *= 0.9  # borda perigosa
        if self.body_confidence_lt is not None:
            gap = self.body_confidence_lt - diag.body_confidence
            if gap > 0.2:
                score *= 0.85
            elif gap < 0.05:
                score *= 0.85
        return score


@dataclass
class ResolutionPattern:
    """Um padrão aprendido de como resolver um tipo de PDF."""

    id: str
    case_type: str
    decision: str  # "pass", "pass_with_warning", "abort"
    resolution_strategy: str
    matching_criteria: MatchingCriteria
    recommended_action: str
    recommended_cli_flags: list[str] = field(default_factory=list)
    residual_risk: str = "medium"  # "low", "medium", "high"
    notes: str = ""
    worked_on_examples: list[dict] = field(default_factory=list)
    times_applied: int = 0
    last_applied: str | None = None  # ISO timestamp

    def to_dict(self) -> dict:
        d = asdict(self)
        d["matching_criteria"] = asdict(self.matching_criteria)
        return d

    @staticmethod
    def from_dict(data: dict) -> ResolutionPattern:
        crit_data = data.pop("matching_criteria", {})
        return ResolutionPattern(
            matching_criteria=MatchingCriteria(**crit_data),
            **data,
        )


@dataclass
class CaseLogEntry:
    """Um caso resolvido, registrado para futuro aprendizado."""

    timestamp: str  # ISO datetime
    pdf_path: str
    raw_hash: str
    diagnostics: dict  # (serializado de PdfDiagnostics.to_dict())
    decision: str
    strategy_id: str | None = None  # ID do padrão aplicado, se houver
    resolution_method: str = "unknown"  # "pattern_match", "human", "default"
    notes: str = ""


@dataclass
class ResolutionMemory:
    """Registry de padrões de resolução + log de casos."""

    version: str = PDF_RESOLUTION_MEMORY_VERSION
    patterns: dict[str, ResolutionPattern] = field(default_factory=dict)
    case_log: list[CaseLogEntry] = field(default_factory=list)

    def find_matching_patterns(
        self, diag: PdfDiagnostics
    ) -> list[tuple[ResolutionPattern, float]]:
        """Encontra padrões que casam com os sintomas, ordenados por confiança.

        Retorna lista de (pattern, confidence_score) ordenada decrescente por score.
        """
        matches = []
        for pattern in self.patterns.values():
            if pattern.matching_criteria.matches(diag):
                score = pattern.matching_criteria.confidence_score(diag)
                matches.append((pattern, score))
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    def register_case(
        self,
        pdf_path: str,
        diagnostics: PdfDiagnostics,
        decision: str,
        strategy_id: str | None = None,
        resolution_method: str = "unknown",
        notes: str = "",
    ) -> None:
        """Registra um caso resolvido para futuro aprendizado.

        Se strategy_id for fornecido e existir no registry, também atualiza
        o padrão com: times_applied, last_applied, e adiciona exemplo.
        """
        raw_hash = sha256_text(Path(pdf_path).read_text(encoding="utf-8", errors="replace"))
        entry = CaseLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            pdf_path=str(pdf_path),
            raw_hash=raw_hash,
            diagnostics=diagnostics.to_dict(),
            decision=decision,
            strategy_id=strategy_id,
            resolution_method=resolution_method,
            notes=notes,
        )
        self.case_log.append(entry)

        # Atualizar padrão com histórico de aplicação
        if strategy_id and strategy_id in self.patterns:
            pattern = self.patterns[strategy_id]
            pattern.times_applied += 1
            pattern.last_applied = entry.timestamp
            # Adicionar exemplo (Path.name para resumir)
            example = {
                "filename": Path(pdf_path).name,
                "raw_hash": raw_hash,
                "timestamp": entry.timestamp,
                "decision": decision,
                "body_confidence": diagnostics.body_confidence,
            }
            pattern.worked_on_examples.append(example)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "patterns": {k: v.to_dict() for k, v in self.patterns.items()},
            "case_log": [asdict(entry) for entry in self.case_log],
        }

    @staticmethod
    def from_dict(data: dict) -> ResolutionMemory:
        patterns = {}
        for k, v in data.get("patterns", {}).items():
            patterns[k] = ResolutionPattern.from_dict(v)
        case_log = [CaseLogEntry(**entry) for entry in data.get("case_log", [])]
        return ResolutionMemory(
            version=data.get("version", PDF_RESOLUTION_MEMORY_VERSION),
            patterns=patterns,
            case_log=case_log,
        )

    def save(self, path: str | Path) -> None:
        """Escreve a memória em JSON."""
        path = Path(path)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def load(path: str | Path) -> ResolutionMemory:
        """Carrega memória de JSON; retorna vazia se arquivo não existir."""
        path = Path(path)
        if not path.exists():
            return ResolutionMemory()
        data = json.loads(path.read_text(encoding="utf-8"))
        return ResolutionMemory.from_dict(data)


def seed_default_patterns() -> ResolutionMemory:
    """Cria a memória com os padrões iniciais (aprendidos do corpus de prova).

    Estes padrões são os "bootstraps" — o sistema começa com este conhecimento
    base e acumula mais conforme processa PDFs.
    """
    mem = ResolutionMemory()

    # Padrão 1: PDF sem camada textual (só-imagem) — aborta
    mem.patterns["image_only_scan"] = ResolutionPattern(
        id="image_only_scan",
        case_type="image_only_scan",
        decision="abort",
        resolution_strategy="abort_no_text_layer",
        matching_criteria=MatchingCriteria(text_page_ratio_lt=0.05),
        recommended_action="Abortar: nenhuma página com camada textual (PDF-scan sem OCR)",
        recommended_cli_flags=["--force-process"],  # se o user quer OCR
        residual_risk="high",
        notes="PDFs escaneados sem OCR prévio — o diagnóstico evita gastar OCR de centenas de páginas.",
    )

    # Padrão 2: Confiança de corpo baixa — aborta
    mem.patterns["low_body_confidence"] = ResolutionPattern(
        id="low_body_confidence",
        case_type="sparse_text_layer",
        decision="abort",
        resolution_strategy="abort_low_body_confidence",
        matching_criteria=MatchingCriteria(body_confidence_lt=0.25),
        recommended_action="Abortar: confiança de corpo principal muito baixa",
        residual_risk="high",
        notes="Corpo muito fragmentado, esparso ou OCR muito ruim — não vale prosseguir.",
    )

    # Padrão 3: OCR ruim — passar com ressalva
    mem.patterns["poor_ocr_quality"] = ResolutionPattern(
        id="poor_ocr_quality",
        case_type="poor_ocr_quality",
        decision="pass_with_warning",
        resolution_strategy="pass_with_warning",
        matching_criteria=MatchingCriteria(
            ocr_risk_ge=0.30,
            body_confidence_ge=0.25,
        ),
        recommended_action="Passar com ressalva: OCR ruim detectado, mas corpo suficiente",
        residual_risk="medium",
        notes="Mojibake, fragmentação de palavra — corpo entra mas requer atenção.",
    )

    # Padrão 4: Front matter pesado no começo — passar com ressalva
    mem.patterns["front_matter_heavy"] = ResolutionPattern(
        id="front_matter_heavy",
        case_type="heavy_front_matter",
        decision="pass_with_warning",
        resolution_strategy="pass_with_warning",
        matching_criteria=MatchingCriteria(
            front_matter_at_head=True,
            body_confidence_ge=0.30,
        ),
        recommended_action="Passar com ressalva: front matter pesado no começo",
        recommended_cli_flags=[],
        residual_risk="low",
        notes="Sumário/índice/capa detectado — o preclean pode remover, corpo é robusto.",
    )

    # Padrão 5: Corpo robusto, sem avisos — passa direto
    mem.patterns["robust_digital_born"] = ResolutionPattern(
        id="robust_digital_born",
        case_type="robust_digital_born",
        decision="pass",
        resolution_strategy="pass_direct",
        matching_criteria=MatchingCriteria(
            # Livros nascidos-digitais têm 5–15% de páginas sem texto (rosto,
            # créditos, divisores, pranchas de imagem); 0.85 admite isso sem
            # confundir com PDF esparso/scan.
            text_page_ratio_ge=0.85,
            body_confidence_ge=0.55,
            ocr_risk_lt=0.15,
        ),
        recommended_action="Passar: corpo robusto, nascido-digital ou bem-convertido",
        residual_risk="low",
        notes="PDF de alta qualidade, poucas ressalvas — segue para as trilhas.",
    )

    return mem
