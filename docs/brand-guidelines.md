# Fiscal da Promo — Guia de Marca v2.0

> Atualizado: 2026-08-23 · Fonte de verdade visual: design do usuário no Claude
> Design ("Artes Fiscal da Promo"); este guia é a transcrição para o pipeline.

## Referência rápida

| Elemento | Valor |
|---|---|
| Nome | **Fiscal da Promo** |
| Handle | `@ofiscaldapromo` (Instagram) · `t.me/fiscaldapromo` (canal do Telegram) — nas artes em CAIXA ALTA mono: `@OFISCALDAPROMO` |
| Conceito | O fiscal que confere o desconto antes de liberar: preço checado no histórico de 12 meses |
| Cores | Navy `#101427` · Dourado `#E0A63C` · Creme `#F6EFE1` · Azul verificado `#7FA0F0` |
| Fontes | Bricolage Grotesque (títulos, preços) · IBM Plex Mono (rótulos, handle, meta) |
| Mascote | Rosto creme com boné navy e lupa, dentro de círculo dourado |

## 1. Paleta

| Nome | Hex | RGB | Uso |
|---|---|---|---|
| Navy | #101427 | 16,20,39 | Fundo das artes escuras, badge de desconto, boné |
| Superfície | #171C33 | 23,28,51 | Cartões, pill do CTA |
| Borda | #262C4A | 38,44,74 | Linhas divisórias |
| Dourado | #E0A63C | 224,166,60 | Círculo do mascote, pill de preço, chips de desconto, seta |
| Tinta sobre dourado | #14110F | 20,17,15 | Texto dentro de elementos dourados |
| Creme | #F6EFE1 | 246,239,225 | Rosto do mascote |
| Texto | #F2F3F7 | 242,243,247 | Títulos e texto principal (tema escuro) |
| Muted | #9AA0B8 | 154,160,184 | Meta, handle, preço riscado (tema escuro) |
| Azul verificado | #7FA0F0 | 127,160,240 | "✓ MENOR PREÇO VERIFICADO" e caixa do selo |
| Areia (tema claro) | #E9E2D6 | 233,226,214 | Fundo das variantes claras 2b/3b |
| Muted claro | #7A776F | 122,119,111 | Meta/handle no tema claro |

Regra: **azul só aparece com prova** (mínima histórica confirmada pela watchlist).

## 2. Tipografia

- **Bricolage Grotesque** (variável, OFL): título 66px w700 (story) / 56px w700
  (feed), preço atual 96px/84px w800, preço original 36px/32px w600 riscado,
  nome da marca 34px w700, CTA 42px w700.
- **IBM Plex Mono** (OFL): handle 26px/22px, meta 30px/27px, rótulos de
  categoria/fonte 21–25px com tracking, selo 25px w500.
- Arquivos em `src/afiliado/assets/fonts/`.

## 3. Logo / mascote

| Arquivo | Uso |
|---|---|
| `brand/avatar-fiscal-dourado.png` | Avatar (Instagram/Telegram): mascote navy em círculo dourado — **principal** |
| `brand/avatar-fiscal-navy.png` | Variante para fundos claros: círculo navy, boné dourado |

O mascote é desenhado por código em `src/afiliado/brand.py` (mesmas
coordenadas do SVG do design), então cabeçalhos das artes e avatar são
sempre idênticos. Não rotacionar, não trocar cores, não adicionar sombra.

## 4. Voz e tom

| Traço | Somos | Não somos |
|---|---|---|
| Fiscal, não vendedor | "conferido no histórico de 12 meses" | "imperdível!!!" |
| Direto | "Creatina 1kg por R$ 68,90" | "oportunidade única chegou" |
| Leve | mascote simpático, "achado", "corre" com moderação | CAPS LOCK em tudo |
| Honesto | selo só com dado; sem "menor preço da história" | inventar preço "de" |

Sempre presente, e desde a fase 5U **em toda peça e em todo texto**, não só na
bio: o chip `LINK DE AFILIADO` na faixa do cabeçalho da arte e a frase
`Link de afiliado — ganho comissão, o preço é o mesmo para você` como PRIMEIRA
linha do texto do Telegram e das legendas do feed, do carrossel e do Reel.
Quem decide as duas é `creative.AFILIADO` / `creative.AFILIADO_NA_ARTE` — um
lugar só, como `pricing.sem_cupom`. A única peça sem o chip é o gráfico do
flagrante: ela não carrega link nenhum.

## 5. Layouts implementados em `creative.py`

- **Story (2a + selo do 1a)** — 1080×1920, padding 72: cabeçalho (mascote ⌀68 +
  nome + chip mono `LINK DE AFILIADO`), card branco 936×790 r28 com foto por contain e badge `-N%` navy,
  título 2 linhas, pill dourada de preço (original riscado + atual), meta mono
  (`45 mil vendidos no último mês · Shopee`, `+250 mil vendidos · Mercado
  Livre` — o texto diz a janela que o número mede), selo azul quando verificado, CTA em pill
  `→ LINK NA SHOPEE` e handle mono no rodapé. O sticker de link vai sobre o CTA.
- **Feed (3a)** — 1080×1350, padding 64: cabeçalho com nome + handle + chip
  `LINK DE AFILIADO`, card 952×600 r26, título, pill de preço, meta, selo,
  rodapé "Link na bio · Shopee" com seta dourada. No slide do carrossel o chip
  divide a faixa com o contador (`2/6`), à direita — eles não se encostam, e um
  teste trava isso.
- **Capa e fecho do carrossel** — sem cabeçalho: o chip entra no BLOCO
  centralizado, depois do subtítulo (capa) e depois da pill `LINK NA BIO`
  (fecho).
- Formatos do design ainda não automatizados: variantes claras (2b/3b), grade
  de 3 (1b) e lista de 6 (1c) — bons para "resumo do dia"; ficam como backlog.

## 6. Bio e descrições (copiar/colar)

**Instagram:** O fiscal confere antes de você comprar 🕵️ Achados de autocuidado,
casa e saúde com preço checado no histórico de 12 meses · Shopee e Mercado
Livre · links de afiliado · ofertas no Telegram 👇

**Telegram:** Fiscal da Promo — ofertas de autocuidado, casa e saúde com o
desconto conferido no histórico de preço antes de postar. Links de afiliado (a
comissão não altera seu preço). Ofertas das 8h às 23h.
