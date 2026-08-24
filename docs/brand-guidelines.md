# PromoProva — Guia de Marca v1.0

> Atualizado: 2026-08-23 · Status: proposta para aprovação

## Referência rápida

| Elemento | Valor |
|---|---|
| Nome | **PromoProva** — "promoção com prova" |
| Handle | `@promoprova` (Instagram) · `t.me/promoprova` (Telegram) — verificar disponibilidade antes de fixar |
| Tagline | Desconto verificado no histórico de preço. |
| Cor primária | Vermelho Promo `#ED1C24` |
| Cor de confiança | Verde Verificado `#2E7D32` |
| Fonte | Anton (títulos, tags, wordmark) · sistema (legendas) |
| Voz | Direta, animada, honesta — nunca inventa desconto |

Alternativas de nome consideradas (mesma identidade visual serve para todas):
**Achado Certo** (`@achadocerto`), **Garimpo Certo** (`@garimpocerto`),
**Preço Provado** (`@precoprovado`), **Promo com Prova** (forma longa).

## 1. Paleta

| Nome | Hex | RGB | Uso |
|---|---|---|---|
| Vermelho Promo | #ED1C24 | 237,28,36 | Tag de preço, CTA, avatar |
| Laranja Prova Social | #E87722 | 232,119,34 | Tag "N mil vendidos", cupom |
| Verde Verificado | #2E7D32 | 46,125,50 | Selo "menor preço verificado", check do logo |
| Carvão | #111111 | 17,17,17 | Fundo de avatar e artes |
| Branco | #FFFFFF | 255,255,255 | Títulos, texto sobre cor |
| Cinza Legenda | #B3B3B3 | 179,179,179 | Handle/assinatura nas artes (70% opacidade) |

Regra: **verde só aparece quando há prova** (mínima histórica confirmada). Nunca
usar o verde como decoração — ele é a promessa da marca.

## 2. Tipografia

- **Anton** (OFL, vendorizada em `src/afiliado/assets/fonts/`): títulos das
  artes (64px story), tags (52px), wordmark. Caixa alta nas tags e no logo;
  título do produto em caixa normal.
- Legendas/captions: fonte do sistema (Instagram/Telegram controlam).
- Story: título máx. 2 linhas × 960px; feed: idem. Truncamento com "…".

## 3. Logo

| Variante | Arquivo | Uso |
|---|---|---|
| A — Tag + selo | `brand/avatar-A-tag.png` | Avatar (recomendado): lê bem em círculo pequeno |
| B — Monograma PP | `brand/avatar-B-monograma.png` | Avatar alternativo, mais minimalista |
| C — Wordmark empilhado | `brand/avatar-C-wordmark.png` | Destaque/capa; menos legível em miniatura |
| Wordmark horizontal | `brand/wordmark-promoprova.png` | Banners, cabeçalho do canal, assinatura |

Regras: não rotacionar, não trocar as cores, não aplicar sombra; área de
respiro = altura do selo verde; tamanho mínimo digital 64px (avatar) / 160px
(wordmark).

## 4. Voz e tom

| Traço | Somos | Não somos |
|---|---|---|
| Direta | "Creatina 1kg por R$ 68,90" | "Oportunidade imperdível chegou!" |
| Animada | emoji com propósito (🔥 🏷️ 👇), 1–2 por post | CAPS LOCK em tudo, 6 emojis por linha |
| Honesta | "menor preço em 7 meses (verificado)" | "menor preço da história" sem dado |
| Próxima | "corre que acaba", "achadinho" | jargão corporativo, formalidade |

Frases proibidas: "imperdível", "última chance" (sem prazo real), "preço de fábrica".
Sempre presente: aviso de link de afiliado na bio/descrição do canal.

## 5. Layout das artes (gerado por `creative.py`)

### Story — 1080×1920

| Zona | y (px) | Conteúdo |
|---|---|---|
| Fundo | 0–1920 | Foto do produto ampliada, blur 28, brilho 38% |
| Card | 150 → até 1200 | Foto do produto, largura 960 (máx. 1050 de altura), cantos 42px |
| Título | card + 70 | Anton 64px branco, até 2 linhas centralizadas |
| Tags | título + 40 | Laranja "N MIL VENDIDOS" (se ≥1000 vendas) → Vermelha "de R$X por R$Y" → Verde "MENOR PREÇO EM N MESES" (só com prova) |
| Sticker de link | ~1700 (fora da arte) | Adicionado no app: "🔗 PEGAR OFERTA" |
| Assinatura | 1800 | `@promoprova` Anton 40px cinza 70% |

### Feed — 1080×1350

| Zona | y (px) | Conteúdo |
|---|---|---|
| Card | 90 → até 770 | Foto, largura 960 (máx. 680 de altura) |
| Título | card + 50 | Anton 64px, 2 linhas |
| Tags | título + 40 | Mesma ordem; a laranja é a primeira a sair se faltar espaço |
| Assinatura | 1280 | `@promoprova` 40px cinza 70% |

Legenda do feed: headline + descrição + título + "De X por Y (N% OFF)" + CTA +
"🔗 Link na bio e no canal do Telegram" — nunca o link de afiliado.

### Telegram (texto + foto)

Foto do produto + legenda HTML: headline / descrição / título / `De: ~X~ | Por:
**Y** (N% OFF)` / selo verificado (quando houver) / CTA / 👉 link.

## 6. Bio e descrições (copiar/colar)

**Instagram (150 chars):** Achadinhos de autocuidado & casa com desconto
verificado no histórico de preço 🏷️ Shopee e Mercado Livre · links de afiliado ·
ofertas no Telegram 👇

**Telegram:** PromoProva — ofertas de autocuidado, casa e saúde com prova de
desconto: comparamos com o histórico de preço antes de postar. Links de
afiliado (a comissão não altera seu preço). Até 48 ofertas por dia, 8h–23h.
