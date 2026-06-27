# Cristal Canônico — especificação da camada canônica do projeto DNA

> Forma cristalizada da camada canônica: a estrutura ideal, reutilizável e
> independente de qualquer autor ou aplicação. Descreve **o contrato**, não uma
> implementação. Qualquer engine que satisfaça este contrato produz um corpo
> canônico válido. Goffman (ou qualquer outro autor) é apenas um *caso de teste*
> desta estrutura — nunca parte dela.
>
> Versão do contrato: `CONTRACT_VERSION = 1.0.0`
> Referência viva: `anchor/gate.py`, `anchor/contract.py`, `pipeline/`.

---

## 1. Princípio do eixo único

A camada canônica existe para produzir **um único eixo confiável** a partir de
um texto bruto, e congelá-lo. Tudo a jusante (trilhas conceitual e formal)
**herda** esse eixo; nada a jusante o renegocia. O eixo nasce único porque
existe **um só portão**, provado uma vez.

```
montante LIVRE        →   PORTÃO (único)   →   eixo congelado   →   a jusante
qualquer engine           contrato C1–C7       book.body.txt        herda
```

A liberdade de montante é deliberada: qualquer rota de implementação serve
(preclean próprio, engine A com âncora, protocolo B com assinatura). Todas
convergem para o mesmo portão. Ou passam (corpo certificado) ou abortam — **não
há terceiro caminho**.

---

## 2. As etapas (forma abstrata)

| # | Etapa | Natureza | Artefato |
|---|-------|----------|----------|
| 0 | **extract** | adaptador de formato → texto | `entrada.raw.txt` + `raw_hash` |
| 1 | **preclean — Nível 1 (genérico)** | limpeza comum a todo material | texto limpo |
| 2 | **PORTÃO (gate)** | certificação do contrato C1–C7 | passa → corpo · ou aborta |
| 3 | **corpo canônico** | eixo único congelado | `book.body.txt` + `body_hash` |
| 4 | **segment** | corpo → parágrafos com spans | `structure.json` |
| 5 | **classify** | rótulo estrutural por bloco | `classification.jsonl` |
| 6 | **chunk** | janelas (conceitual + formal) | `chunks.jsonl` · `chunks.formal.jsonl` |
| 7 | **quality** | filtro determinístico de qualidade | `chunks.quality.jsonl` (+ formal) |
| 8 | **eligibility — Nível 2 (por trilha)** | limpeza específica, pós-portão | filtro de elegibilidade |

O **portão (etapa 2)** é a fronteira da camada canônica em sentido estrito:
antes dele, limpeza; nele, certificação; o resto (4–8) é preparação
determinística do eixo já certificado para as trilhas.

---

## 3. O contrato do portão — C1 a C7 (o coração do cristal)

O portão prova invariantes sobre o corpo. Nenhuma é sobre conteúdo; todas são
sobre **integridade estrutural e proveniência**. É o que torna o corpo
reutilizável e reprodutível independentemente do autor.

| Check | Nome | Invariante |
|-------|------|------------|
| **C1** | eixo round-trip | `strip_sentinels(body[char_start:char_end]) == paragraph.text` para **todo** parágrafo |
| **C1'** | cobertura | spans sem sobreposição e sem lacuna (exceto espaço em branco / sentinelas) — o corpo é coberto inteiro pelo eixo |
| **C2** | sentinelas | sentinelas de bloco isolados em linha própria, balanceados; chamadas de nota (noteref) balanceadas |
| **C3** | hashável | corpo é hashável de forma estável |
| **C4** | isolamento | nenhum vazamento de ruído residual no corpo (o repertório de ruído não reaparece) |
| **C5** | estabilidade entre execuções | mesma entrada + mesmo contrato → mesmo corpo (provado por teste diferencial) |
| **C6** | proveniência congelada | `source_format`, `source_edition` e `translation_layer` presentes (pode ser `None`, mas declarado) |
| **C7** | tamanho mínimo | corpo ≥ `min_size` (em `words` ou `chars`) — abaixo disso, aborta |

C3 e C5 juntos são a garantia de reprodutibilidade: o corpo é uma função pura
de `(bruto, contrato, engine)`.

---

## 4. Congelamento e proveniência

O corpo é selado por uma **chave de congelamento**:

```
freeze_key = f( raw_hash , anchor_filling_hash , engine_version )
```

- `raw_hash` — hash do texto bruto de entrada
- `anchor_filling_hash` — hash do repertório de ruído + assinatura de front matter (se aplicável)
- `engine_version` — versão do motor que produziu o corpo

Mudar qualquer um dos três **re-bakeia o corpo de forma controlada** (novo
`body_hash`). Não mudar nenhum → o mesmo corpo, byte a byte. Isso é o que
permite que o eixo seja "congelado": ele é rastreável até suas condições de
produção.

O contrato de contexto (`AnchorCtx`) carrega a proveniência declarada:
`source_format`, `source_edition`, `translation_layer`, `min_size`,
`size_unit`, `residual_noise_patterns`, e flags de front matter.

---

## 5. Limpeza em dois níveis

A limpeza **não é um procedimento único**. O cristal separa dois níveis por
posição em relação ao portão:

- **Nível 1 — genérico / canônico (antes do portão).** Comum a todo material de
  entrada. 5 estágios em ordem — trim de front matter (opt-in), strip de
  cabeçalho/rodapé, remoção de numeração de página, dehyphenate, reflow — mais o
  repertório `CANONICAL_NOISE_PATTERNS` e a **proteção de sentinela** transversal
  a todos os estágios (sentinela nunca é removida nem vira cabeçalho). Entra no
  `freeze_key`, logo é parte da identidade do corpo.

- **Nível 2 — específico por trilha (depois do portão).** Age sobre os **chunks
  já certificados**, não sobre o bruto. O portão certifica o corpo genérico;
  cada trilha define seus próprios critérios de elegibilidade. Um só
  `book.body.txt` alimenta trilhas diferentes sem reprocessar o bruto. Reside em
  `pipeline/chunk_eligibility.py`. **Decisão de arquitetura (opção B)**: a
  alternativa de limpar o bruto por trilha *antes* do portão foi rejeitada
  porque exigiria conhecer a trilha de destino na entrada, quebrando o eixo
  único.

---

## 6. O que herda a jusante

Após o portão, o eixo único é preparado por etapas **determinísticas, sem LLM**:

- **segment** — o corpo vira parágrafos com `char_start`/`char_end` exatos (o
  span que C1 prova).
- **classify** — cada bloco recebe uma de cinco classes estruturais mínimas
  (`navigation_noise`, `editorial_discourse`, `main_body`, `quotation_material`,
  `footnote_material`), derivadas de sentinela + regime de front matter, nunca de
  palpite sobre a superfície.
- **chunk** — duas janelas sobre a mesma estrutura: conceitual (~janela menor) e
  formal (~janela maior, para estilometria).
- **quality** — filtro determinístico de 11 flags sobre os chunks.

A classe (etapa 5) é o que **alimenta** a elegibilidade por trilha (etapa 8): é
por ela que a trilha decide o que é matéria-prima sua e o que descarta.

---

## 7. A fronteira — o que NÃO é canônico

Esta seção é o que mantém o cristal *abstrato*. Nada abaixo pertence à camada
canônica; se aparecer dentro dela, é resíduo a extrair:

- **Vocabulário conceitual de um autor** (conceitos, operações, famílias). Isso é
  da trilha conceitual, não do canônico. — *resíduo conhecido: `CONCEPT_SPECS`,
  `OPERATION_SPECS`, `_revelation_utility()` em `conceptual.py` ainda carregam
  Goffman.*
- **Critérios de voz / estilometria.** São da trilha formal.
- **Cenas, simulação, julgamento por LLM.** São aplicação, não canônico.
- **Limiares de elegibilidade específicos de uma trilha.** Pertencem ao Nível 2,
  parametrizados por trilha — não ao corpo.

O teste de pertencimento ao cristal: *"isto seria igual para qualquer autor e
qualquer trilha?"* Se não, está fora.

---

## 8. Checklist de replicabilidade

Para reaplicar o canônico a um novo autor/sistema, basta:

1. Um **adaptador de extract** para o formato de entrada.
2. Um **repertório de ruído** Nível 1 (parte canônico, parte extensível via
   `--noise-pattern`).
3. Declarar a **proveniência** (`source_format`, `source_edition`,
   `translation_layer`, `min_size`, `size_unit`).
4. Passar o **portão C1–C7**. Se passar, há corpo. Se não, aborta — e o motivo é
   um dos sete checks, nomeado.

Nada disso menciona quem é o autor. É essa indiferença ao conteúdo que faz do
canônico um cristal.
