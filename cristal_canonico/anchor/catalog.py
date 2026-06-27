"""Catálogo — sedimento, não cardápio (spec 5.4).

NÃO é biblioteca consultada-antes para escolher âncora (isso reintroduz o
classificador e prolifera bespoke). É registro do que cada texto EXIGIU,
escrito DEPOIS que a escada rodou. Lookup só por raw_hash.

Destrava: usa-se-existe (lookup), auditabilidade, re-bake controlado
(contrato mudou → quais contract_version defasadas → revalida só esses), e
medição empírica da superfície bespoke real (quantos canonical/config/hook).

Backing opcional em JSONL; sem path, fica em memória.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .contract import AnchorCtx


class AnchorCatalog:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._by_id: dict[str, dict] = {}
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._by_id[entry["text_id"]] = entry

    def lookup(self, raw_hash: str) -> dict | None:
        """Degrau 1 — usa-se-existe. Único modo de consulta."""
        return self._by_id.get(raw_hash)

    def register(
        self,
        text_id: str,
        *,
        rung: str,
        filling_kind: str,
        filling_ref: str | None,
        ctx: AnchorCtx,
        body_size: int,
        body_hash: str,
        gate_passed: bool = True,
    ) -> dict:
        """Escrito-depois-registra (nunca consultado-antes-classifica)."""
        entry = {
            "text_id": text_id,
            "rung": rung,
            "filling": {"kind": filling_kind, "ref": filling_ref},
            "provenance": ctx.provenance_dict(),
            "body_size": body_size,
            "gate": {"passed": gate_passed, "contract_version": ctx.contract_version},
            "body_hash": body_hash,
        }
        self._by_id[text_id] = entry
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def stats(self) -> dict:
        """Medição empírica da superfície bespoke (quantos por degrau)."""
        return dict(Counter(e["rung"] for e in self._by_id.values()))

    def stale(self, current_contract_version: str) -> list[str]:
        """text_ids com contract_version defasada — alvos de re-bake."""
        return [
            tid
            for tid, e in self._by_id.items()
            if e["gate"]["contract_version"] != current_contract_version
        ]
