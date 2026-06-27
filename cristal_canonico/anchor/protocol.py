"""Assinatura uniforme da âncora — protocolo B (spec 5.5).

Comece pelo protocolo B: toda âncora é um módulo com a mesma assinatura +
portão, sem desduplicar. Colapse para Engine A (config declarativa) só
quando o reuso for provado (≳70% compartilhado). B é estritamente mais
barato e nunca é desperdício (spec Seção 6).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pipeline.preclean import preclean

from .contract import AnchorCtx


@runtime_checkable
class Anchor(Protocol):
    def to_canonical_body(self, raw: str, ctx: AnchorCtx) -> str:
        """Devolve o corpo canônico a partir do bruto.

        Contrato: a saída DEVE passar validate_body_static +
        validate_roundtrip. Proveniência: DEVE preencher
        source_format / source_edition / translation_layer (via ctx).
        """
        ...


class CanonicalAnchor:
    """Preenchimento trivial como âncora protocolo-B de referência.

    preclean canônico + cp = a rota canônica (Degrau 2). Serve de exemplo
    concreto da assinatura uniforme; novos ports copiam esta forma.
    """

    def to_canonical_body(self, raw: str, ctx: AnchorCtx) -> str:
        body, _ = preclean(raw, ctx)
        return body
