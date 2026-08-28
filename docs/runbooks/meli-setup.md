# Runbook — Setup Mercado Livre (fase 3B/5B — pool curado com régua + preço do buy box + links em lote)

Checklist para habilitar a fonte de ofertas do Mercado Livre (`sources.meli`
em `config.yaml`, desligada por padrão).

Quando travar em qualquer passo, abra o Claude Code e peça ajuda citando o
número do passo.

---

## 0. Como a fonte funciona (leia antes de mexer)

A descoberta de itens **não** usa mais a busca pública do ML — ela devolve
403 na API real. O fluxo é:

```
pool curado (data/meli_offers.json) — gerado por /meli-pool-refresh (JoomPulse)
        │  fetch_offers: leitura local, sem rede; VALIDA cada entrada
        │  (régua completa: mediana, p25, janela, mínima, buy box) e conta
        │  no aviso, por motivo, o que pulou
        ▼
   seleção/ranking (igual às outras fontes)
        │
        ▼
   refresh_price: GET /products/{id}/items — preço vivo do anúncio que
        │         vence o BUY BOX (buy_box_item_id do pool), imediatamente
        │         antes de publicar. Anúncio ausente da lista → oferta
        │         descartada ("sem buy box"); NUNCA o menor preço entre os
        │         vendedores. O preço vivo entra no price_log (o histórico
        │         próprio do ML é só de preços vivos).
        ▼
   pricing.verdict: regra do quartil (modo A/B) + selo estrito, uma vez;
        │           texto, arte, legendas e copy recebem o veredito pronto
        ▼
   resolve_affiliate_link: lê data/meli_links.json (pool de links)
```

### Endpoints confirmados contra a API real

- **Liberados** (`https://api.mercadolibre.com`, `Authorization: Bearer`):
  - `GET /highlights/MLB/category/{catId}` — usado na curadoria externa do
    pool (fora do escopo deste runbook; ver "Alimentando o pool" abaixo).
  - `GET /products/{productId}` — `permalink` vem **vazio**; a URL do
    produto é sempre montada como `https://www.mercadolivre.com.br/p/{id}`.
    A chave `buy_box_winner` existe na resposta mas veio **`null`** em todas
    as verificações ao vivo (2026-08-26: token de aplicação E de usuário,
    com e sem `?attributes=buy_box_winner`, 3 produtos do pool) — este app
    não recebe o vencedor por aqui.
  - `GET /products/{productId}/items` — usado por `refresh_price`; traz o
    preço por vendedor (até 100 por página, `paging.total`). O pipeline lê
    SÓ o item cujo `item_id` é o `buy_box_item_id` do pool (o `buyBoxId` do
    JoomPulse — confirmado presente na lista real: MLB7125449388 a
    R$ 104,90 entre 37 vendedores de MLB66637233 cujo menor preço era
    R$ 58,90). `original_price` quase sempre é `null` — não dá para
    calcular desconto por aqui. **A ordem da lista NÃO é o buy box** (ver
    "Buy box ao vivo" abaixo): `results[0]` não serve de vencedor.

### Buy box ao vivo (2026-08-26, rodada de correção da 5B)

Três produtos do pool, comparando a PÁGINA real (`/p/{id}` lida pelo Chrome
com sessão logada — o `GET` por `httpx` com User-Agent de navegador é
redirecionado para `gz/account-verification`, sem preço no HTML), o
`results[0]` de `/products/{id}/items` (token de aplicação) e o anúncio do
pool (`buyBoxId` do JoomPulse lido no MESMO dia):

| produto | página real (buy box) | `results[0]` da API | anúncio do pool |
|---|---|---|---|
| MLB66637233 Creatina Growth 500g | MLB4555189589 — **R$ 78,90** (de 104,90) | MLB4555189589 @ 78,90 ✅ | MLB7125449388 @ 104,90 — na lista (38 vendedores, menor 58,90), mas **não é o da página** ❌ |
| MLB26796581 Creatina Dark Lab 500g | MLB4812143184 — **R$ 49,90** | MLB4812143184 @ 49,90 ✅ | MLB4812143184 @ 49,90 ✅ (único vendedor) |
| MLB68104527 Kit Body Splash | MLB4645102377 — **R$ 109,90** (R$ 93,41 no Pix; de 140) | MLB4991164827 @ 89,00 ❌ (a página mostra `results[1]`) | MLB4683756059 — **ausente** da lista (9 vendedores) ❌ |

Conclusões que o código segue:

- `results[0]` bateu em 2 de 3 — a lista não é "ordenada por buy box";
  `refresh_price` continua lendo o anúncio do pool, nunca `results[0]`.
- O anúncio do pool pode deixar de vencer (linha 1) ou sumir (linha 3) no
  mesmo dia em que o JoomPulse o reportou. O que dá para garantir é a IDADE
  da verificação: o leitor exige `buy_box_checked_at` (na falta, vale
  `generated_at`) com **no máximo 7 dias**; vencido, a entrada é ignorada com
  motivo "buy box não verificado há N dias". O skill `/meli-pool-refresh`
  tem um "Passo semanal — checar buy box" (4 consultas) que renova a data.
- O buy box que a página mostra pode depender de CEP/sessão; o pipeline
  publica o preço do anúncio que nomeia, e o descarte "sem buy box" (anúncio
  fora da lista) continua agrupado no resumo de ops.
- **Bloqueados (403, não usar)**: `/sites/MLB/search`, `/items/{id}`.
- **Geração de link de afiliado**: não há API pública — é o endpoint interno
  do painel (`/affiliate-program/api/v2/affiliates/createLink`), autenticado
  por sessão via cookies do navegador, não por OAuth. Ver seção "Pool de
  links de afiliado" abaixo.

## 1. Criar o app em developers.mercadolivre.com.br

- [ ] Acesse https://developers.mercadolivre.com.br e faça login com a
      conta do Mercado Livre/Mercado Pago que vai operar a integração.
- [ ] **Minhas aplicações** → **Criar aplicação**. Nome: ex. "Fiscal da
      Promo". Preencha os campos obrigatórios (descrição, URL de callback —
      pode ser qualquer URL válida, ex. a do próprio repositório, já que o
      fluxo desta fase não depende de redirect interativo em produção).
- [ ] Após criar, anote **Client ID** e **Client Secret** — viram
      `MELI_CLIENT_ID` / `MELI_CLIENT_SECRET`.

## 2. Autenticação — duas estratégias (nesta ordem)

O pipeline tenta primeiro `client_credentials` (sem estado, funciona só com
Client ID/Secret — ideal para CI/produção). Se o app não tiver esse grant
liberado, ele cai para `refresh_token`. A autenticação é usada só por
`refresh_price` (a leitura do pool, `fetch_offers`, não chama a rede).

### 2a. `client_credentials` (tente primeiro)

- [ ] Teste direto:
```bash
curl -X POST https://api.mercadolibre.com/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"grant_type":"client_credentials","client_id":"<CLIENT_ID>","client_secret":"<CLIENT_SECRET>"}'
```
- [ ] Se a resposta trouxer `access_token`, é só configurar
      `MELI_CLIENT_ID`/`MELI_CLIENT_SECRET` — não precisa do passo 2b.

### 2b. `refresh_token` (fallback, se o app não suportar client_credentials)

- [ ] Gere uma autorização (fluxo de usuário, uma vez só): monte a URL
      abaixo com o Client ID e a URL de callback cadastrada no app, abra no
      navegador logado na conta do Mercado Livre e autorize:
```
https://auth.mercadolivre.com.br/authorization?response_type=code&client_id=<CLIENT_ID>&redirect_uri=<CALLBACK_URL>
```
- [ ] O redirect traz um `code` na query string. Troque por tokens:
```bash
curl -X POST https://api.mercadolibre.com/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"grant_type":"authorization_code","client_id":"<CLIENT_ID>","client_secret":"<CLIENT_SECRET>","code":"<CODE>","redirect_uri":"<CALLBACK_URL>"}'
```
- [ ] A resposta traz `access_token` e `refresh_token` — anote o
      `refresh_token`, vira `MELI_REFRESH_TOKEN`.
- [ ] **Atenção:** o Mercado Livre rotaciona o `refresh_token` a cada uso.
      O pipeline já lida com isso sozinho — persiste o token novo em
      `data/meli_token.json` (gitignored) a cada troca e usa o arquivo como
      fonte preferencial nas próximas execuções, caindo para
      `MELI_REFRESH_TOKEN` só se o arquivo ainda não existir (primeiro run).
      Não edite `data/meli_token.json` manualmente.

## 3. Pool curado (`data/meli_offers.json`) — formato da fase 5B

`fetch_offers` só **lê** este arquivo. Quem o gera é o skill
**`/meli-pool-refresh`** (`.claude/skills/meli-pool-refresh/`), com o
JoomPulse: top produtos por categoria, título/imagem e o histórico semanal
do anúncio que vence o buy box. Formato:

```json
{"generated_at": "2026-08-26", "valid_days": 30,
 "source": "JoomPulse (vendas = estimativa) — ...",
 "offers": [{
   "product_id": "MLB18725310", "title": "...", "image_url": "...",
   "category": "MLB264586", "buy_box_item_id": "MLB3928374651",
   "buy_box_checked_at": "2026-08-26",
   "price_ref_cents": 2590, "price_p25_cents": 2428, "price_window_days": 91,
   "price_historic_min_cents": 1699, "price_min_window_days": 365,
   "sales": 13337, "rating": 4.8}]}
```

- `buy_box_checked_at` — data em que o `buyBoxId` foi lido. Vale **7 dias**
  (ver "Buy box ao vivo"); ausente, vale `generated_at`.
- `price_ref_cents` — **mediana** das médias semanais do anúncio do buy box
  (nunca a foto de um dia: no pool antigo a "referência" era o preço de UM
  vendedor num dia, e 9 de 38 itens tinham ref ≥ 2,5× a mínima — C7). Vira
  `Offer.price_ref_cents` e `price_current_cents` inicial (substituído pelo
  preço vivo em `refresh_price`).
- `price_p25_cents` — 25º percentil da mesma janela (para baixo). O post só
  alega desconto quando o preço vivo fica ESTRITAMENTE abaixo dele.
- `price_window_days` — dias distintos que sustentam ref/p25 (7 × semanas
  observadas). Menos de 14 → nunca modo A.
- `price_historic_min_cents` / `price_min_window_days` — mínima histórica do
  buy box e a janela dela. Viram `Offer.price_floor_cents` e
  `price_floor_window_days`; o selo é estrito ("Menor preço dos últimos 12
  meses (verificado)" só quando preço vivo ≤ mínima). A mínima é sempre um
  preço DIÁRIO que existiu: quando a mínima histórica do cubo é de outro
  anúncio (vencedor mudou) o skill gasta uma consulta na série diária
  (`/meli-pool-refresh`, Passo 3b) — **média semanal nunca vira mínima**, ela
  é ≥ a menor diária e o selo sairia para um preço acima de um que existiu.
  E, no run, o piso curado ainda cede ao nosso próprio price_log quando ele
  viu mais barato (a observação própria só baixa o piso).
- `buy_box_item_id` — o anúncio cujo preço o pipeline publica.
- `sales` é o contador **VITALÍCIO** do próprio Mercado Livre (`catalogSales`) —
  o "+250 mil vendidos" do anúncio, e por isso `Offer.sales_e_faixa` é verdadeiro
  e `Offer.sales_window_days` é 0. NÃO é `catalogOrderCount1m` (a estimativa
  mensal do JoomPulse): ela já esteve neste campo e pôs "5 mil vendidos" num
  story de um produto com 250 mil. A Shopee é o contrário — o `sales` dela mede
  ~30 dias, e o texto diz isso. Ver `.claude/skills/meli-pool-refresh/SKILL.md`.

### Validação na carga (o que o leitor rejeita, e diz por quê)

Entrada **pulada e contada no aviso, por motivo**: campo de preço ausente,
nulo, textual ou negativo (`sem referência` / `sem p25` / `sem janela da
referência` / `sem mínima histórica` / `sem janela da mínima`); campo de preço
com FRAÇÃO de centavo (`4500.5` → `não inteiro`; o float integral `2590.0` é
aceito como 2590 — JSON não distingue os dois); régua **parcialmente** zerada
(`régua parcial`); `price_ref_cents / 100`
fora de `selection.price_min_brl..price_max_brl` (`fora da faixa de preço` —
o item a R$ 19,90 do pool antigo morria em silêncio em todo run);
`price_p25_cents > price_ref_cents` (`p25 acima da referência`);
`price_historic_min_cents > price_p25_cents` (`mínima acima do p25` — sinal
de que o vencedor do buy box mudou e a mínima é de outro anúncio); sem
`buy_box_item_id` (`sem buy box`); `buy_box_checked_at` com mais de 7 dias
(`buy box não verificado há N dias`) ou inválida/no futuro (`data do buy box
inválida`); `product_id` repetido. O aviso sai assim,
no `doctor` e no resumo de ops: `3 entrada(s) do pool ignorada(s) (2 fora da
faixa de preço, 1 sem p25)`. Um pool no formato antigo é rejeitado inteiro
(`38 entrada(s) do pool ignorada(s) (38 sem p25)`) — não é zero silencioso.

### Entrada SEM HISTÓRICO (fase 5J)

A entrada cujos **cinco** campos de régua vêm presentes e iguais a 0 é
ACEITA: é a "onda barata" do `/meli-pool-refresh`, que pula o histórico
(4 consultas do JoomPulse a cada 28 produtos, contra 1 a cada 50 do resto) e
enche o pool em dias em vez de semanas. Ela publica em **modo B** — preço +
prova social, sem alegar desconto e sem selo — e ganha régua sozinha quando o
nosso `price_log` tiver `selection.ref_min_observations` dias distintos
(degrau 3 de `pricing.enrich_offers`).

- Campo **ausente** continua sendo erro: o que se aceita é o zero EXPLÍCITO,
  senão um typo de curadoria passaria a valer como "sem histórico".
- Zero **parcial** continua sendo erro (`régua parcial`): `ref > 0` com
  `p25 = 0` é curadoria quebrada, não histórico faltando.
- A faixa de preço é checada sobre a referência, que não existe aqui: para
  essas entradas ela é **adiada**, não removida — `validate.check_price` a
  aplica sobre o preço VIVO depois do `refresh_price`, com os mesmos números.
- O `doctor` e o resumo de ops imprimem a proporção: `🏷️ Mercado Livre: 12 de
  35 entrada(s) com régua curada; 23 em modo B esperando histórico`.
- **Quanto demora a graduação, medido:** o preço do ML só entra no
  `price_log` quando a oferta passa pelo `refresh_price` — ou seja, quando é
  escolhida para publicar — e o dedupe de 30 dias a tira da fila em seguida.
  São ~1 observação por item a cada 30 dias: os 14 dias distintos levam ~14
  MESES, não 14 dias. Na prática, a onda barata é modo B permanente até que
  alguém decida observar o preço do pool fora do caminho de publicação.

- Desde a fase 4 o ML não tem teto de preço próprio: quem decide
  publicabilidade é `selection.max_above_ref` (não anunciar item mais caro que
  o típico) + `validate.check_price`, igual para as duas lojas.
- Arquivo ausente, JSON inválido, ou vencido (`generated_at` + `valid_days`
  no passado) → `fetch_offers` devolve lista vazia **sem levantar exceção**;
  o pipeline segue normalmente só com as demais fontes, e acrescenta o aviso
  `⚠️ meli: 0 ofertas buscadas — pool vencido: …` no resumo do run. Rode
  `/meli-pool-refresh` e depois `/meli-links-refresh`.

### Cota do JoomPulse (fato, para os dois skills)

O plano tem limite de consultas por dia, compartilhado entre
`query_cubejs_meli` e `query_cubejs_shopee`; `read_resource` não conta.

**Teto medido em 2026-08-26** (a cota renova todo dia, confirmado pelo dono):

| Momento | Consultas com dado | Onde parou |
|---|---|---|
| antes da renovação | 6 | 7ª → `MCP subscription request limit exceeded` |
| depois da renovação | **9** | 10ª → mesmo erro |

Ou seja: **~9 consultas por dia**. Isso é pouco e tem consequência de projeto:

| Tarefa | Consultas | Dias a 9/dia |
|---|---|---|
| Pool do ML (validade 30 dias) | ~33 | 4 |
| Checagem semanal do buy box | 4 | menos de 1 |
| Referências de preço da Shopee (~120 itens) | ~40 | 5 |

Por isso o JoomPulse **não é** a fonte principal de referência de preço da
Shopee — o histórico próprio do pipeline (`price_log`, custo zero, 14 dias de
observação) é. O JoomPulse semeia os itens mais quentes e cuida do pool do ML,
que a API pública não consegue fornecer.

Os dois skills têm a seção "Orçamento": `max_consultas` (padrão **9**),
trabalho em **ondas fechadas** (um lote levado do começo ao fim, para que
parar por cota deixe um artefato válido e menor, nunca um pela metade),
resultado bruto salvo em `data/joompulse_raw/<skill>/<data>/` ANTES da próxima
consulta, cursor em `data/joompulse_raw/<skill>/cursor.json`, e retomada sem
repetir consulta. Registre aqui o próximo "limit exceeded" com data e número.

## 4. Pool de links de afiliado (`data/meli_links.json`)

Não existe API oficial de geração de link de afiliado no Mercado Livre —
diferente da Shopee, cuja mutação GraphQL gera o link automaticamente. O
endpoint real é interno do painel de afiliados (confirmado por teste manual:
aceita lote, idempotente por produto+tag, devolve o mesmo link do painel) e
autentica por **sessão via cookies do navegador**, não por OAuth.

- [ ] `meli.tag` em `config.yaml` precisa ser uma etiqueta **já cadastrada**
      no painel de afiliados (linkbuilder) — tag inexistente faz o item
      falhar (`total_error`) sem quebrar o lote inteiro.
- [ ] Rode o skill **`/meli-links-refresh`** (`.claude/skills/meli-links-refresh/`)
      sempre que o pool curado (`data/meli_offers.json`) trouxer produtos
      novos, ou quando o `doctor`/o resumo do run avisar pool vazio/vencido.
      Ele lê os `product_id` do pool que ainda não têm link, pede a sessão
      do painel (cookies + `x-csrf-token`, via ferramentas do Chrome ou
      colados do DevTools) e chama `gerar_links` (`src/afiliado/meli_links.py`)
      em lotes, mesclando o resultado em `data/meli_links.json` sem nunca
      sobrescrever um link já existente.
- [ ] **Rotina mensal:** a sessão do painel expira; rode `/meli-links-refresh`
      pelo menos uma vez por mês (ou sempre que o skill reportar sessão
      expirada) para recolher cookies frescos e cobrir os produtos que
      entraram no pool desde a última rodada.
- [ ] Item sem entrada no pool de links é descartado silenciosamente pelo
      pipeline (comportamento já existente: promove a próxima oferta da
      fila) — não é um erro que derruba o run.
- [ ] **O arquivo não vem com o repositório.** Em 2026-08-26 ele não existia
      em nenhum checkout e nunca tinha sido commitado, enquanto este runbook
      dizia "é commitado" — resultado: com `sources.meli: true` num clone
      limpo, TODA oferta do ML virava descarte e o `doctor` imprimia ✅
      (fase 5C, A6). Ele PRECISA ser **gerado** (`/meli-links-refresh`) e
      depois **commitado** (`git add data/meli_links.json`): é público (links
      de afiliado, não segredo), e sem o commit a VPS e o Actions continuam
      sem link nenhum. Só os **cookies da sessão** nunca são gravados em
      arquivo nem commitados (ver o skill).
- [ ] O que o `doctor` mostra desde a fase 5C: `X de Y produto(s) do pool com
      link`. Cobertura ZERO com a fonte ligada é ❌ (o ML não publicaria
      nada); cobertura parcial é ⚠️. E o run avisa uma vez por dia no chat de
      operações quando menos da metade do pool tem link.

## 5. Configurar e ligar

- [ ] `.env` local e GitHub Secrets: `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`
      e, se aplicável (passo 2b), `MELI_REFRESH_TOKEN`.
- [ ] `config.yaml` → `sources.meli: true`.
- [ ] `afiliado doctor` → deve mostrar `✅ Mercado Livre: token ok; N
      oferta(s) válida(s) no pool`. Sem credenciais, mostra uma linha
      informativa (não falha o doctor). Entradas puladas, pool vazio ou
      vencido mostram `⚠️` com a contagem e o motivo por grupo — é a mesma
      validação que o run faz.

## Notas

- `commission_pct` das ofertas do Mercado Livre vem de `meli.commission_pct`
  em `config.yaml` — uma **estimativa média** (padrão `4.0`, ou seja, 4%),
  não a taxa real por item: nenhum dos endpoints liberados expõe comissão
  por item. **Ajuste esse valor com as taxas reais do seu painel de
  afiliados** (variam por categoria); um valor desatualizado ou zerado
  distorce o ranking por valor esperado (`ev_score`) e pode fazer o ML
  perder posição para a Shopee — ou, com `selection.min_ev_brl` ativo, ser
  descartado direto pelo piso.
- `rating`/`sales` das ofertas do Mercado Livre vêm do pool curado
  (`sales` é estimativa do JoomPulse), não de uma chamada ao vivo — refletem
  o momento da curadoria, não o instante da publicação (só o preço é
  atualizado ao vivo, via `refresh_price`, e só o do anúncio do buy box).
- O `price_log` do ML guarda apenas preços VIVOS (gravados logo após
  `refresh_price`); o preço com que a oferta sai do pool é a mediana e não é
  registrado como observação. O histórico próprio, portanto, só cresce para
  itens que chegam ao topo da fila.
- O histórico do JoomPulse é do anúncio que vence o buy box HOJE: se o
  vencedor mudou dentro da janela, a série é a do vencedor atual (e a mínima
  histórica do produto pode ser de outro anúncio — o leitor rejeita `mínima >
  p25`; o skill usa a mínima semanal nesse caso e conta quantos foram).
## Token do ML no runner efêmero (Actions) — testado ao vivo

Cada execução do workflow começa do zero, sem `data/meli_token.json` entre
runs. Se a autenticação caísse no fluxo `refresh_token` (passo 2b), a rotação
se perderia a cada execução e a autenticação quebraria na seguinte — e o
Actions precisaria gravar o token novo de volta no secret (`gh secret set` com
um PAT de escopo `repo`).

**Não precisa.** Teste ao vivo em 2026-08-26, com as credenciais reais e
`data/meli_offers.json` do pool:

| passo | resultado |
|---|---|
| `POST /oauth/token` com `grant_type=client_credentials` | **HTTP 200**, `expires_in` 21600 s (6 h), escopo com `read` e `offline_access` |
| `GET /products/MLB66637233/items` com esse token | **HTTP 200**, 38 vendedores, preços reais |
| `GET /products/MLB26796581/items` | **HTTP 200**, 1 vendedor |
| `GET /products/MLB68104527/items` | **HTTP 200**, 9 vendedores |

Ou seja: o endpoint que o `refresh_price` usa aceita o **token de aplicação**.
Como `MeliSource._authenticate` tenta `client_credentials` PRIMEIRO e só cai
para `refresh_token` se ele for recusado, no Actions o refresh token nunca é
usado nem rotacionado — não há estado a preservar, e o workflow **não precisa
de `GH_PAT` nem de `gh secret set`**.

`MELI_REFRESH_TOKEN` continua nos secrets só como rede de segurança. Se um dia
o `client_credentials` passar a ser recusado (app reconfigurado, grant
revogado), o sintoma será `meli: autenticação falhou` no chat de operações a
partir do segundo run — e aí, sim, ou o ML volta a rodar só na VPS
(`docs/runbooks/vps-setup.md`, onde `data/meli_token.json` sobrevive), ou o
workflow ganha um passo de escrita de volta no secret com um PAT:

- [ ] GitHub → Settings → Developer settings → Personal access tokens →
      **Fine-grained token**, repositório único, permissão **Secrets:
      Read and write** (e `Contents: Read and write`, que o workflow já usa).
- [ ] Cadastre como secret `GH_PAT` e acrescente ao workflow, depois do run:
      `gh secret set MELI_REFRESH_TOKEN < <(jq -r .refresh_token data/meli_token.json)`
      com `GH_TOKEN: ${{ secrets.GH_PAT }}` no `env:`.
- [ ] Um PAT com escrita em secrets é uma chave que abre o repositório
      inteiro — só crie se o `client_credentials` realmente parar.
