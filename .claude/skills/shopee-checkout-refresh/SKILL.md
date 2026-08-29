---
name: shopee-checkout-refresh
description: Enche `checkout_prices` em data/watchlist.json com o preço EXIBIDO da Shopee (o de checkout, com cupom) lido do cubo ShbMartItem do JoomPulse — 100 itens por consulta, sem navegador e sem sessão. Rodar UMA vez por dia, de manhã, antes do grosso dos posts; ou quando o doctor/o run avisar que a seção está vazia ou vencida. Requer o conector JoomPulse (claude.ai) autenticado na sessão e gasta 1 a 2 consultas da cota diária.
---

# O preço com cupom da Shopee, pelo cubo

O post publica **R$ 689,99**; o dono abre o anúncio e vê **R$ 611,80**. Os dois
números estão certos — o segundo exige cupom (às vezes cupom mais Pix) e é
desconto de CHECKOUT, que a API de afiliados não expõe por nenhuma das sete
rotas do `docs/runbooks/shopee-preco.md`.

A **oitava rota** expõe: `ShbMartItem.price` do JoomPulse É o preço exibido.

| item | nossa API (`productOfferV2`) | `ShbMartItem.price` | a página mostrava |
|---|---|---|---|
| 16892189215 | R$ 689,99 | **611,80** | R$ 611,80 |
| 23598844177 | R$ 599,00 | **523,48** | R$ 523,48 |

Sem navegador, sem sessão, **sem risco para a conta** — e 100 itens por
consulta, contra os ~4 do cubo de histórico do `/shopee-regua-refresh`.

**A conta e as guardas estão em código**, em `afiliado.shopee_checkout`, com
teste (`tests/test_shopee_checkout.py`). Este skill é só a coleta: o preço sai
do comando, não da sua cabeça, e nenhum número é digitado à mão.

## Pré-requisitos

- Conector **JoomPulse** disponível (ferramentas `mcp__claude_ai_JoomPulse__*`;
  carregue via ToolSearch se estiverem deferidas). Sem ele, avise e pare.
- Executar da raiz do repo.
- Regras de consulta: leia `pulse://rules` (`read_resource`, não conta na cota)
  se for a primeira vez na sessão.
- `ShbMartItem.price` é **preço raspado, real** — ao contrário dos campos
  `sold*`/`revenue*`, que são estimativas do JoomPulse.

## Orçamento (obrigatório)

O plano do JoomPulse tem **limite diário de consultas e ele é PEQUENO**: medido
em 2026-08-26, **9 consultas** após a renovação (a 10ª devolveu `MCP
subscription request limit exceeded`). A cota é da assinatura e é compartilhada
com `query_cubejs_meli` e com os outros skills; `read_resource` e
`list_resources` não contam.

**Cabem 100 itens por consulta** (medido em 2026-08-29: 120 ids no filtro,
`limit: 100` → exatamente 100 linhas; `offset: 100` → as 17 restantes). O grão
de `ShbMartItem` é o ITEM: uma linha por anúncio, sem intervalos, sem risco de
item cortado.

- Parâmetro `max_consultas` por execução: **padrão 2**. Nunca ultrapasse sem o
  usuário pedir. Duas consultas cobrem 200 itens — mais do que a fila que o
  pipeline publica em um dia (≈60 posts).
- **Rodar UMA vez por dia basta, e rodar mais NÃO adianta.** A data que a
  guarda usa é `itemLastSeenDate`, o dia em que a **Shopee** foi raspada pelo
  JoomPulse — não o dia da nossa coleta. Consultar de novo à tarde devolve
  exatamente os mesmos preços com exatamente a mesma idade.
- **Ondas fechadas**: um lote de até 100 itens levado do começo ao fim
  (consulta → bruto salvo → `coletar` → arquivo gravado) antes do próximo.
  Parar por cota deixa uma watchlist completa e menor, nunca metade de uma.
- **Cada resultado bruto é salvo ANTES da próxima consulta**, em
  `data/joompulse_raw/shopee-checkout/<AAAA-MM-DD>/<lote>_p<pagina>.json`
  (o JSON exato devolvido; o diretório está no `.gitignore`). Bruto não salvo é
  consulta gasta e perdida.
- Ao atingir `max_consultas` OU receber "limit exceeded": **PARE**, diga
  quantas consultas fez e quais itens ficaram pendentes.

## Passo 0 — quais itens coletar (0 consultas)

Os que o pipeline **publicaria**: o topo de `selection.order_by_ev` sobre o
estoque vivo, sem quem já tem um preço de checkout dentro do teto de idade
(reconsultá-lo não mudaria nada hoje).

```
PYTHONPATH=src python -m afiliado.shopee_checkout alvos --n 100
```

Ele lê o `data/state.db` (sem rede), aplica os mesmos portões do run
(`filter_offers`), ordena por EV e imprime os ids — um por linha e, no fim, a
lista JSON pronta para colar no filtro do cubo. Rode-o **antes de cada onda**:
o estoque gira a cada 15 minutos.

## Passo 1 — o preço exibido (1 consulta por 100 itens)

Cubo `ShbMartItem` via `query_cubejs_shopee`. Fatos já verificados — não gaste
consulta redescobrindo:

- o grão é **um item por linha** (`itemId`); `price` é MEASURE (`avg`), então
  ela vai em `measures`, não em `dimensions`;
- **`itemLastSeenDate` é obrigatório na consulta.** É a idade do dado, e a
  coleta RECUSA qualquer linha sem ela. Sem a idade o preço de dias atrás seria
  apresentado como o de hoje;
- `ShbMartItem.itemId` é a mesma chave que `Offer.item_id`. Não há conversão;
- a cobertura do cubo não é censo: só itens com ao menos uma venda na vida,
  categoria resolvida e preço abaixo de R$ 50.000. Medido em 2026-08-29,
  **117 dos 120** ids do topo da nossa fila estavam lá.

```json
{"dimensions":["ShbMartItem.itemId","ShbMartItem.itemLastSeenDate"],
 "measures":["ShbMartItem.price"],
 "filters":[{"member":"ShbMartItem.itemId","operator":"equals",
             "values":["<até 100 ids>"]}],
 "order":[["ShbMartItem.itemId","asc"]],
 "limit":100,"offset":0}
```

**Página cheia (100 linhas) NÃO corta item nenhum** — ao contrário do cubo de
histórico. Ela só quer dizer que sobraram itens do filtro: pagine com
`offset: 100` (custa outra consulta) e salve como `_p1.json` no mesmo
diretório, ou deixe-os para amanhã.

**O formato da resposta é COLUNAR** (`columns` + `data` de listas), não a lista
de dicionários. Salve o JSON como veio — `afiliado.joompulse` lê as duas
formas. Se anexar a consulta que você mandou sob a chave `query`, o comando
passa a saber o `limit` que ela usou.

## Passo 2 — gravar (0 consultas)

```
PYTHONPATH=src python -m afiliado.shopee_checkout coletar data/joompulse_raw/shopee-checkout/<AAAA-MM-DD> --dry-run
```

Sem `--dry-run` ele grava a seção `checkout_prices` em `data/watchlist.json`,
cada entrada com o `price_cents` e o `measured_at` da **raspagem**. Ele recusa,
com motivo na tela:

- **linha sem `itemLastSeenDate` legível** — sem idade não há entrada;
- **preço zero, negativo ou ilegível**;
- **raspagem no futuro** (relógio nosso ou do cubo fora de lugar).

O que ele **não** mexe: `generated_at`, `category_boosts`, `hot_items`,
`price_refs` e `price_floors`. Coletar preço de checkout não revisa boost
nenhum, e regravar a data do arquivo afirmaria que sim. Quem passa a dizer
"hoje" é `section_dates.checkout_prices`. **Não edite o arquivo à mão.**

## O que a guarda faz DEPOIS, no run — e por que quase metade não publica

Gravar não é publicar. Em cada post, `shopee_checkout.avalia` compara o preço
do cubo com o preço **vivo** que o `refresh_price` acabou de medir, e só
carimba quando as três portas abrem:

| porta | regra | por quê |
|---|---|---|
| é menor | cubo `<` vivo | preço do cubo maior é preço velho de anúncio que barateou, não cupom |
| distância sã | 1% a **15%** abaixo | ver abaixo |
| recente | raspagem há no máximo **3 dias** | o cubo é uma foto por item, e ela envelhece |

**Os 15%, com os números.** Medido em 2026-08-29 sobre 117 itens do topo da
fila real: 105 tinham o preço do cubo menor que o vivo e **85 desses ficam em
até 15%**, empilhados nos degraus de cupom da Shopee (~5%, ~10% e exatamente
14,50%). Há um cotovelo claro entre 14,57% e 15,34%; acima dele os valores são
espalhados e a cauda vai a **55,84%** — R$ 199,90 virando R$ 88,27 não é cupom,
é o preço tendo mudado desde a raspagem. A medição independente do dono, em 10
itens conferidos contra a tela, deu a mesma faixa (5% a 14,5%).

**Os 3 dias, com os números.** O cubo é reconstruído todo dia, mas com a última
raspagem de CADA item: na mesma amostra, `itemLastSeenDate` teve **mediana de 3
dias e máximo de 30**, e só 36 dos 117 tinham sido vistos nas últimas 24 h.

**Resultado medido: 50 dos 117 itens publicariam o preço de checkout hoje.** Os
outros 67 caem quase todos pela idade (56) — e é por isso que rodar a coleta
duas vezes no mesmo dia não muda nada: a idade é da raspagem da Shopee, não da
nossa consulta. Os limiares estão em `preco_checkout:` no `config.yaml`, com os
números ao lado; subir `idade_max_dias` para 4 levaria a cobertura de 50 para
86 dos 117 — é decisão do dono, não deste skill.

Fora das três portas o post publica **o preço da API, como sempre**. Errar por
baixo custa uma peça sem o preço com cupom; errar por cima custa prometer um
preço que ninguém paga, e é esse erro que a conta não sobrevive.

## Passo 3 — conferir, resumir, commitar

1. Validar com o mesmo leitor do pipeline e a mesma conta da guarda:
```
PYTHONPATH=src python -c "from datetime import date; from afiliado.watchlist import load_watchlist; from afiliado import shopee_checkout as sc; wl=load_watchlist('data/watchlist.json'); print(len(wl.checkout_prices),'precos,',len(sc.frescos(wl.checkout_prices,date.today(),3)),'dentro do teto de idade; secao de',wl.section_date('checkout_prices'),'| regua de',wl.section_date('price_refs'))"
```
   Seção malformada degrada para vazio SEM quebrar o resto do arquivo — confira
   os NÚMEROS impressos, não só a ausência de erro. `price_refs`, `price_floors`
   e `hot_items` têm de continuar lá.
2. `afiliado doctor` (opcional, e ele não abre navegador nenhum) tem um item
   `preco_checkout` que repete essa contagem.
3. Resumo ao usuário: quantos preços entraram, quantos caíram e por qual guarda,
   **quantos publicariam hoje** (é o número que importa, não o total gravado),
   quantas consultas foram gastas, o que ficou pendente.
4. Commit: `chore: preco de checkout da Shopee (dados JoomPulse de <data>)`,
   terminando com o `Co-Authored-By:` do modelo em uso. `git add` com caminho
   EXPLÍCITO: `data/watchlist.json` e mais nada — `data/state.db` é escrito pela
   produção a cada 15 min e não entra na sua mudança; `data/joompulse_raw/` é
   gitignored.

## Notas

- **Nunca inventar números**: todo valor gravado vem de uma linha salva em
  `data/joompulse_raw/`, passada pelo `coletar`.
- **Não afrouxe os limiares** para "cobrir mais itens". O preço que o post
  afirma é a superfície de maior risco do projeto; o piso, o teto e a idade
  existem porque cada um deles tem uma cauda medida do outro lado.
- **Só a Shopee.** O preço que publicamos do Mercado Livre é o do anúncio que
  vence o buy box, exatamente o que a página mostra; a guarda recusa qualquer
  oferta do ML antes de olhar o resto.
- **A leitura do navegador (fase 5P) vence esta.** Se `preco_real.enabled` for
  ligado um dia, o que ele carimbar fica: é leitura viva, ancorada na frase da
  própria página. O cubo só preenche o que ela não carimbou.
- Este skill NÃO substitui `/shopee-regua-refresh`: aquele semeia
  `price_refs`/`price_floors` (a régua do quartil e o selo, cubo
  `ShbModelsPricesDaily`, ~4 itens por consulta) e este só o preço exibido. Os
  dois dividem a mesma cota — se ela estiver curta, este rende muito mais por
  consulta.
