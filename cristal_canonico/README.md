# Cristal Canônico — pacote isolado

Recorte **autocontido** da camada canônica do projeto DNA: o fecho transitivo do
portão (`pipeline/run.py`), sem nada da aplicação nem das trilhas. Produz **um
único eixo confiável** a partir de texto bruto e o congela.

Especificação completa do contrato: [`docs/cristal_canonico.md`](docs/cristal_canonico.md)
(`CONTRACT_VERSION = 1.0.0`) · diagrama em `docs/cristal_canonico.svg`/`.png`.

## O que está aqui (as 9 etapas, 0–8)

| Camada | Arquivos |
|--------|----------|
| **core** (fundação) | `core/sentinels.py`, `core/text.py` (freeze_key, hashes, strip_sentinels) |
| **anchor** (o PORTÃO) | `anchor/gate.py` (C1–C7), `anchor/contract.py` (AnchorCtx, CONTRACT_VERSION), `anchor/dispatch.py`, `anchor/catalog.py`, `anchor/protocol.py` |
| **pipeline** (etapas) | `extract` · `preclean` (+`noise`,`front_matter`) · `segment` · `classify` · `chunk` · `quality` · `chunk_eligibility` (Nível 2) · `run` (orquestrador) · `pdf_diagnostics` + `pdf_resolution_memory` (pré-condição do extract) |
| **schemas** | `anchor_catalog` · `chunks` · `structure` · `pdf_resolution_patterns` |

## O que NÃO está aqui (a fronteira — §7 da spec)

Removido de propósito, por não ser canônico ("isto seria igual para qualquer autor
e qualquer trilha?" — se não, está fora):

- `pipeline/conceptual.py` — vocabulário conceitual (trilha conceitual; resíduo Goffman)
- `pipeline/cross_voice/`, `pipeline/formal/`, `pipeline/formal_trail.py` — voz/estilometria (trilha formal)
- `pipeline/trails.py` — despachante de trilha
- `aula/`, `cena/`, e todo o tutor/professora/termômetro — aplicação
- **testes** — não incluídos neste recorte (a pedido)

## Como rodar

A pasta preserva a estrutura de pacotes (`core`/`anchor`/`pipeline`), então basta
rodar **de dentro desta pasta** (o cwd entra no `sys.path`):

```bash
cd cristal_canonico
python3 -P -m pipeline.run <entrada.epub|html|txt|pdf> -o <saida/> \
        --source-format epub --source-edition "..." --translation-layer none \
        --min-size 500 --size-unit words
```

O portão é a única porta de saída válida: ou `book.body.txt` (passou) ou **abort**
nomeando o check C1–C7 que falhou — sem terceiro caminho.

## Proveniência deste recorte

- Copiado de `projeto-dna/` (os originais permanecem intactos lá).
- Isolamento **verificado**: os 19 módulos importam da cópia local e nenhum
  módulo de trilha (`conceptual`/`trails`/`formal`/`cross_voice`) é carregado;
  `python -m pipeline.run --help` carrega o CLI com as opções do contrato.
- Para virar pacote instalável, basta um `pyproject.toml` com
  `packages = ["core", "pipeline", "anchor"]`.
