---
name: shopee-regua-refresh
description: Semeia a régua de preço da Shopee em data/watchlist.json (price_refs e price_floors) com o histórico do JoomPulse — mediana, p25, mínima e a janela REAL em dias, expandidos dos intervalos do cubo ShbModelsPricesDaily. Rodar quando as candidatas da Shopee estiverem todas em modo B por falta de referência, ou para renovar as réguas mais velhas. Requer o conector JoomPulse (claude.ai) autenticado na sessão e respeita a cota diária, que é pequena (~36 itens por dia).
---

# Semeadura da régua de preço da Shopee

Enche `price_refs` e `price_floors` de `data/watchlist.json` com o que o
JoomPulse mediu do preço de cada anúncio: **mediana** (o "de" que o post pode
alegar), **p25** (o topo do quartil mais barato, que a regra do quartil exige
furar), **mínima** (o selo de menor preço) e a **janela real em dias**.

O pipeline já lê tudo isso — `pricing.enrich_offers` consulta
`watchlist.price_refs[item_id]` e `price_floors[item_id]` ANTES de recorrer ao
histórico próprio. Não invente caminho novo: encher o arquivo basta.

**A conta e as guardas estão em código**, em `afiliado.shopee_regua`, com
teste (`tests/test_shopee_regua.py`). Este skill é a coleta; a régua sai do
comando, não da sua cabeça — mediana e percentil feitos à mão numa conversa
são exatamente o tipo de número que ninguém consegue conferir depois.

## Por que isto existe (medido em 2026-08-28)

**Zero** das 4.799 candidatas da Shopee podia alegar desconto. O arquivo tinha
23 `price_floors` e **nenhuma** `price_refs`; o `price_log` próprio tinha 3
dias distintos contra os 14 que a regra do quartil exige, e sozinho só
chegaria lá por volta de 11/09.

## Pré-requisitos

- Conector **JoomPulse** disponível (ferramentas `mcp__claude_ai_JoomPulse__*`;
  carregue via ToolSearch se estiverem deferidas). Sem ele, avise e pare.
- Executar da raiz do repo. Leia `data/watchlist.json` atual para comparar depois.
- Regras de consulta: leia `pulse://rules` (`read_resource`, não conta na cota)
  se for a primeira vez na sessão.
- Os preços deste cubo são **raspados, reais** — ao contrário dos campos
  `sold*`, que são estimativas do JoomPulse e precisam ser divulgados como tais.

## Orçamento (obrigatório) — a cota é o projeto inteiro

O plano do JoomPulse tem **limite diário de consultas e ele é PEQUENO**:
medido em 2026-08-26, **9 consultas** após a renovação (a 10ª devolveu
`MCP subscription request limit exceeded`); antes dela, 6. A cota é da
assinatura e é compartilhada com `query_cubejs_meli`; `read_resource` não conta.

**Cabem ~4 itens por consulta** (medido em 2026-08-28): o teto é de 100 linhas
e cada item tem de 7 a 50 intervalos. Com 9 consultas/dia isso dá **~36 itens
por dia** — o orçamento do dia é esse, e ele é o motivo de o Passo 0 existir:
escolher QUAIS 36 vale mais que qualquer outra coisa neste skill.

- Parâmetro `max_consultas` por execução: **padrão 9**. Nunca ultrapasse.
- **Trabalhe em ONDAS fechadas**: um lote de ~4 itens levado do começo ao fim
  (consulta → bruto salvo → `semear` → arquivo gravado) antes de começar o
  próximo. Parar por cota deixa então uma watchlist completa e menor, nunca
  metade de uma.
- **Cada resultado bruto é salvo ANTES da próxima consulta** em
  `data/joompulse_raw/shopee-regua-refresh/<AAAA-MM-DD>/<lote>_p<pagina>.json`
  (o JSON exato devolvido, com a chave `query`; o diretório está no
  `.gitignore`). O `semear` lê desse diretório — bruto não salvo é consulta
  gasta e perdida.
- `data/joompulse_raw/shopee-regua-refresh/cursor.json` guarda onde parou:
  `{"data": "...", "consultas_feitas": N, "lote": k, "itens_pendentes": [...],
  "limite_observado": "<mensagem ou null>"}`. Atualize a cada consulta.
- Ao atingir `max_consultas` OU receber "limit exceeded": **PARE**, grave o
  cursor, diga quantas consultas fez e o que ficou pendente. A próxima
  execução retoma dali sem repetir consulta.

## Passo 0 — quais itens semear (0 consultas)

Os que o pipeline **publicaria**: o topo de `selection.order_by_ev` sobre o
estoque vivo de candidatas, sem quem já tem referência. Não use `rank_offers`
— ele é dimensionado por `posts_per_run` e devolve um punhado.

```
PYTHONPATH=src python -m afiliado.shopee_regua alvos --n 36
```

Ele lê o `data/state.db` (sem rede), aplica os mesmos portões do run
(`filter_offers`), ordena por EV e imprime os ids — um por linha e, no fim, a
lista JSON pronta para colar no filtro do cubo. Rode-o **antes de cada onda**:
o estoque gira, e o topo de hoje não é o de ontem.

## Passo 1 — o histórico de preço (1 consulta por ~4 itens)

Cubo `ShbModelsPricesDaily` via `query_cubejs_shopee`. Fatos já verificados —
não gaste consulta redescobrindo:

- **o grão é o INTERVALO** `(itemId, priceStart, priceEnd, modelPrice)`, não um
  ponto por dia. `modelId` é sempre 0 e não carrega informação;
- **o cubo não tem measures**: consulte como linhas cruas;
- preços em BRL, reais (raspados). Retenção ~365 dias, mas a coleta da Shopee
  começou na primavera de 2026 — há ~6 meses de histórico;
- `ShbMartItem.itemId` é a mesma chave que `Offer.item_id`. Não há conversão.

```json
{"dimensions":["ShbModelsPricesDaily.itemId","ShbModelsPricesDaily.modelPrice",
               "ShbModelsPricesDaily.priceStart","ShbModelsPricesDaily.priceEnd"],
 "filters":[{"member":"ShbModelsPricesDaily.itemId","operator":"equals",
             "values":["<id1>","<id2>","<id3>","<id4>"]}],
 "order":[["ShbModelsPricesDaily.itemId","asc"],
          ["ShbModelsPricesDaily.priceStart","asc"]],
 "limit":100,"offset":0}
```

**Página cheia (100 linhas) quer dizer item cortado.** Com a ordem acima, o
último `itemId` da página perdeu os intervalos MAIS RECENTES — a régua dele
sairia de uma janela que não existiu. O `semear` recusa esse item sozinho
("linhas cortadas no fim da página cheia"); para recuperá-lo, pagine com
`offset: 100` (custa outra consulta) e salve a página seguinte no mesmo
diretório, ou deixe-o para a onda seguinte, sozinho no lote. **Nunca grave a
régua de um item cortado.**

## Passo 2 — expandir os intervalos e gravar (0 consultas)

```
PYTHONPATH=src python -m afiliado.shopee_regua semear data/joompulse_raw/shopee-regua-refresh/<AAAA-MM-DD> --dry-run
```

Sem `--dry-run` ele grava em `data/watchlist.json`. O que o comando faz, e por
que cada parte é assim:

1. **expande cada intervalo nos dias que ele cobre**, recortando na borda da
   janela de 90 dias antes de contar. É a expansão que dá peso ao preço que
   durou 60 dias contra o que durou 1 — sem ela, o "de" inflado do vendedor
   (um dia a R$ 68,90 num item que custa R$ 26 há três meses) entra na conta
   como se fosse preço;
2. **dia sem linha é dia NÃO OBSERVADO** e não entra na janela. A janela é o
   que o selo publica ("menor preço dos últimos N dias"); esticá-la até o
   intervalo seguinte seria mentir sobre quantos dias nós medimos. Intervalo
   sem `priceEnd` vale UM dia, o do início, pelo mesmo motivo;
3. mediana e p25 são os de `pricing` (os mesmos do pipeline), a mínima é o
   menor preço da expansão e a janela é o número de dias observados. Centavos
   com `Decimal`/`ROUND_FLOOR`, sempre para baixo.

### As guardas — o que ele recusa, e por quê

- **menos de 14 dias observados: a entrada NÃO é gravada.** Régua curta é pior
  que régua ausente — ela autoriza alegação sobre uma janela que não mediu
  nada. É o `pricing.MIN_WINDOW_DAYS`, o mesmo número da regra do quartil.
- **mínima nunca acima do p25** (e p25 nunca acima da referência). É a mesma
  regra que o leitor do pool do ML aplica, pelo mesmo motivo: mínima alta
  demais vira selo inventado.
- **item que o cubo não devolve fica de fora dos dois mapas.** Silêncio, não
  zero: sem linhas a entrada não existe, e a oferta continua publicável em
  modo B.
- **item cortado no fim da página cheia** (ver o Passo 1).

Cada recusa sai na tela com o motivo. Anote-as no resumo: elas são metade do
resultado do dia.

### O que ele NÃO mexe

`generated_at`, `category_boosts` e `hot_items` saem intactos. Semear a régua
não revisa boost nenhum, e regravar a data do arquivo afirmaria que sim — os
`hot_items` trazem razões escritas no dia em que foram medidos. Quem passa a
dizer "hoje" é `section_dates.price_refs`/`price_floors`, e cada entrada leva
a data dela em `measured_at`. Ondas de dias diferentes convivem sem que uma
envelheça a outra. **Não edite o arquivo à mão** — o formato é responsabilidade
do `afiliado.watchlist`, com teste.

## Passo 3 — conferir, resumir, commitar

1. Validar com o mesmo leitor do pipeline:
```
PYTHONPATH=src python -c "from afiliado.watchlist import load_watchlist; wl=load_watchlist('data/watchlist.json'); print(len(wl.hot_items),'itens,',len(wl.price_refs),'referencias,',len(wl.price_floors),'pisos; opiniao de',wl.section_date('hot_items'),'| regua de',wl.section_date('price_refs'))"
```
   Seção malformada degrada para vazio SEM quebrar o resto do arquivo — confira
   os NÚMEROS impressos, não só a ausência de erro. Os 23 `hot_items` e os 23
   pisos antigos têm de continuar lá.
2. Resumo ao usuário: quantos itens entraram, quantos caíram e por qual guarda,
   quantas consultas foram gastas, o que ficou pendente no cursor. Diga também
   quantos ficariam em **modo A hoje** — provavelmente nenhum, e isso é a régua
   funcionando (leia a seção seguinte antes de comemorar ou de se assustar).
3. Commit: `chore: semeia a regua da Shopee (dados JoomPulse de <data>)`,
   terminando com o `Co-Authored-By:` do modelo em uso. `git add` com caminho
   EXPLÍCITO: `data/watchlist.json` e mais nada — `data/state.db` é escrito
   pela produção a cada 15 min e não entra na sua mudança;
   `data/joompulse_raw/` é gitignored.

## Expectativa honesta — diga isto no resumo

**Semear NÃO faz o modo A aparecer de imediato.** Conferido nos três itens
medidos à mão em 2026-08-28 (`11503789697`: 68 dias, mediana R$ 152,91, p25
R$ 129,97, mínima R$ 129,97; `22097495429`: 42 dias, 199,90/159,91/101,91;
`22493114640`: 32 dias, 410,01/406,50/381,00): **nenhum** ficaria em modo A,
porque a regra exige preço ESTRITAMENTE abaixo do quartil mais barato e
nenhum está. Isso é a régua funcionando.

O que a semeadura entrega desde o primeiro dia:

1. **o selo volta a ser possível** — piso com janela medida, em vez de janela 0
   (que não emite selo nenhum);
2. **o `selection.max_above_ref` passa a filtrar de verdade.** Este é o maior
   ganho da fase: a bicicleta `22493114640` está a R$ 504,90 com típico de
   R$ 410,01 e hoje nós a publicaríamos, porque sem referência não há com o
   que comparar. Semeada, ela cai no portão;
3. o modo A passa a ser possível **nos dias em que for merecido**.

Quem esperar fogos no primeiro dia vai achar que a fase falhou.

## Notas

- Nunca inventar números: todo valor gravado vem de uma linha salva em
  `data/joompulse_raw/`, passada pelo `semear`.
- **Não afrouxe `ref_min_observations` nem a janela mínima** para fazer o modo
  A aparecer. O número existe porque com 5 observações "mais da metade dos
  dias" eram 3 dias (C8).
- A referência da watchlist tem **precedência** sobre o histórico próprio do
  `StateDB` (degrau 2 contra degrau 3 de `pricing.enrich_offers`): uma
  referência errada aqui não é corrigida pelo pipeline, ela substitui o que
  ele mediria sozinho. O piso, esse, ainda pode ser BAIXADO pelo nosso
  `price_log` — nunca subido.
- Watchlist vencida perde só `category_boosts`/`hot_items`; refs e pisos são
  fatos datados e continuam valendo. Renovar a régua não renova a opinião, e
  `is_stale` continua medindo a opinião.
- Renove a régua dos itens mais velhos quando sobrar cota: `measured_at` diz
  quando cada entrada foi medida. Item que mudou de faixa de preço com a
  referência velha vira desconto errado — para cima é que é grave.
- Este skill NÃO substitui `/watchlist-refresh`, que é quem mexe em
  `category_boosts` e `hot_items` (e cujo Passo 3 faz esta mesma conta à mão,
  para os itens quentes). Quando os dois puderem rodar, prefira este para a
  régua: a conta aqui é código testado.
