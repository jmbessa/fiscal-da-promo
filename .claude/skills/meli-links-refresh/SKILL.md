---
name: meli-links-refresh
description: Gera links de afiliado POR ANÚNCIO para os produtos do pool curado do Mercado Livre (data/meli_offers.json), cunhando no painel interno (sessão por cookies) os 3 anúncios mais baratos de cada produto que passam no piso de qualidade e ainda não estão em data/meli_links.json. Rodar quando o pool for atualizado, quando o doctor avisar cobertura baixa/pool vencido, ou uma vez por mês para renovar os anúncios que sumiram. Requer cookies de uma sessão logada em mercadolivre.com.br — via ferramentas do Chrome ou colados manualmente do DevTools — e as credenciais MELI_CLIENT_ID/MELI_CLIENT_SECRET do .env.
---

# Links de afiliado do Mercado Livre, um por ANÚNCIO

Preenche `data/meli_links.json` (lido por `MeliSource.refresh_price` e
`resolve_affiliate_link`) com links curtos do painel de afiliados — **um para
cada anúncio**, não um por produto.

## Por que por anúncio (fase 5M)

Um link de `/p/MLB...` abre a página de catálogo, e é o Mercado Livre que
escolhe ali qual vendedor o seguidor vê. Foi assim que um story saiu com
R$ 80,00 num produto cuja página mostrava R$ 39,90, e outro com R$ 209,87 num
de R$ 113. O vencedor do buy box **não é obtível** pela API (`buy_box_winner`
vem `null`; o campo `tier` de `/products/{id}/items` veio vazio nos 89
anúncios sondados) e o preço também não dá para conferir depois
(`/items/{id}` e `/sites/MLB/search` são 403; a página com sessão é uma casca
sem preço).

A saída é linkar o anúncio: o pipeline publica o preço do anúncio linkado mais
barato e dá o link **daquele** anúncio. Post e clique passam a mostrar o mesmo
número por construção.

O painel aceita a URL do anúncio — medido em 2026-08-28:
`https://produto.mercadolivre.com.br/MLB-7080290072-_JM` cunhou
`https://meli.la/2WFwu8s`, e o link abre o card daquele anúncio com o mesmo
preço que `/products/{id}/items` dá para ele. O link curto **não é
construtível** (o `ref` do link completo é um token opaco do servidor): cada um
precisa passar pelo painel, e é por isso que este skill existe.

## Formato do arquivo

```json
{
  "version": 2,
  "generated_at": "2026-08-28",
  "tag": "ofiscaldapromo",
  "products": {
    "MLB18725310": {
      "items": {"MLB7381404798": "https://meli.la/aaaa",
                "MLB4555189589": "https://meli.la/bbbb"},
      "product_link": "https://meli.la/1ULuAEY"
    }
  }
}
```

- `items` — o que publica: `item_id -> link do anúncio`. É daqui que sai o
  preço e o link do post.
- `product_link` — os 55 links por PRODUTO da fase 5C (etiqueta `jmbessa`).
  Continuam guardados porque continuam válidos e foram trabalho de painel, mas
  **não publicam nada**: eles abrem o catálogo. Não apague, não use.
- Leia e escreva sempre com `afiliado.meli_links.ler_pool` /
  `escrever_pool` — o formato antigo (`{product_id: link}`) é migrado na
  leitura.

**Os cookies da sessão nunca são commitados nem gravados em arquivo** — ficam
só na memória desta sessão do Claude Code, usados apenas para autenticar as
chamadas feitas agora. Só `data/meli_links.json` (links públicos, sem segredo)
vai para o disco, e ele **precisa ser commitado**: sem isso a VPS e o Actions
ficam sem link e cada oferta do ML vira descarte (fase 5C, A6).

## Pré-requisitos

- Executar da raiz do repo.
- `MELI_CLIENT_ID` / `MELI_CLIENT_SECRET` no `.env` — o Passo 1 lê
  `/products/{id}/items` com token de aplicação (`client_credentials`).
- A etiqueta de `config.yaml` → `meli.tag` (hoje `ofiscaldapromo`) **já
  cadastrada** no painel; etiqueta inexistente faz o item falhar
  (`total_error`) sem quebrar o lote.
- Uma sessão logada em `mercadolivre.com.br` — ver Passo 2.

## Passo 1 — Quais anúncios precisam de link

Para cada `product_id` do pool curado, os **3 anúncios mais baratos que passam
no piso de qualidade** entram na lista. Quem decide é
`afiliado.sources.meli.anuncios_para_linkar` — a MESMA função que a publicação
usa para escolher o preço, para que não se cunhe link de anúncio que a
publicação recusaria.

Escreva o script com a ferramenta **Write** e rode com
`PYTHONPATH="$PWD/src" python <script>`:

```python
import json
import os
import time
from pathlib import Path

import httpx

from afiliado import config
from afiliado.meli_links import ler_pool
from afiliado.sources.meli import LINKS_POR_PRODUTO, anuncios_para_linkar

cfg = config.load_config("config.yaml")
me = cfg["meli"]
pool = json.loads(Path(me["offers_path"]).read_text(encoding="utf-8"))
links = ler_pool(me["links_path"])

client = httpx.Client(timeout=30)
tok = client.post("https://api.mercadolibre.com/oauth/token", json={
    "grant_type": "client_credentials",
    "client_id": os.environ["MELI_CLIENT_ID"],
    "client_secret": os.environ["MELI_CLIENT_SECRET"]}).json()["access_token"]
headers = {"Authorization": f"Bearer {tok}"}

faltantes, por_produto = [], {}
for offer in pool["offers"]:
    pid = offer["product_id"]
    results, offset = [], 0
    for _ in range(5):                     # 100 por página; o maior tem 277
        r = client.get(f"https://api.mercadolibre.com/products/{pid}/items",
                       headers=headers, params={"offset": offset} if offset else None)
        if r.status_code != 200:
            print(f"{pid}: HTTP {r.status_code}")
            break
        data = r.json()
        results += data.get("results") or []
        paging = data.get("paging") or {}
        offset += int(paging.get("limit") or 0)
        if offset >= int(paging.get("total") or 0):
            break
        time.sleep(0.2)
    escolhidos = anuncios_para_linkar(results, LINKS_POR_PRODUTO)
    ja_tem = set((links.get(pid) or {}).get("items", {}))
    por_produto[pid] = escolhidos
    faltantes += [i for i in escolhidos if i not in ja_tem]
    time.sleep(0.2)

Path("data/_meli_anuncios.json").write_text(
    json.dumps({"por_produto": por_produto, "faltantes": faltantes}), encoding="utf-8")
print(f"{len(faltantes)} anúncio(s) a cunhar em {len(por_produto)} produto(s)")
```

Se `faltantes` vier vazio, informe o usuário e pare — nada a fazer.
`data/_meli_anuncios.json` é um arquivo de trabalho: **apague no fim** e não
commite.

## Passo 2 — Obter a sessão (cookies + CSRF token)

Duas opções, tente a primeira:

**A) Ferramentas do Chrome** (`mcp__claude-in-chrome__*`; carregue via
ToolSearch se estiverem deferidas):
1. Confirme com o usuário que há uma aba logada em `mercadolivre.com.br`
   (peça para abrir e logar se não houver).
2. Navegue até `https://www.mercadolivre.com.br/afiliados/linkbuilder`.
3. Gere um link manualmente pela UI (ou deixe o usuário gerar um) para
   provocar uma chamada real a `createLink`.
4. Use `read_network_requests` para capturar essa chamada e ler os headers
   `cookie` e `x-csrf-token` que o próprio navegador enviou.
5. Se o cabeçalho `cookie` não vier completo (alguns cookies são `HttpOnly` e
   não aparecem via JS, mas devem aparecer nos headers reais da requisição de
   rede), prossiga para a opção B.

**B) Colar manualmente** (sempre funciona, peça ao usuário):
1. Peça para abrir `https://www.mercadolivre.com.br/afiliados/linkbuilder`
   logado, abrir o DevTools → aba **Network**, gerar um link qualquer pela UI,
   e localizar a requisição para `createLink`.
2. Peça para copiar e colar aqui o cabeçalho **`cookie`** completo e o
   **`x-csrf-token`** dessa requisição (aba Headers → Request Headers).

Nunca ecoe os cookies de volta na conversa além do necessário para o passo
seguinte, e nunca os escreva em arquivo.

## Passo 3 — Cunhar e mesclar

Escreva outro script com **Write** (não heredoc de shell — cookies/CSRF têm
caracteres que o bash pode corromper):

```python
import json
from pathlib import Path

from afiliado import config
from afiliado.meli_links import escrever_pool, gerar_links, ler_pool

cfg = config.load_config("config.yaml")
me = cfg["meli"]
trabalho = json.loads(Path("data/_meli_anuncios.json").read_text(encoding="utf-8"))

links, erro = gerar_links(
    trabalho["faltantes"],
    tag=me["tag"],
    cookies="<COOKIE_HEADER_DO_PASSO_2>",
    csrf="<X_CSRF_TOKEN_DO_PASSO_2>",
)

pool = ler_pool(me["links_path"])
for pid, anuncios in trabalho["por_produto"].items():
    entrada = pool.setdefault(pid, {"items": {}, "product_link": ""})
    for item_id in anuncios:
        if links.get(item_id):
            entrada["items"][item_id] = links[item_id]   # nunca sobrescreve com vazio
escrever_pool(me["links_path"], pool, tag=me["tag"])

print(f"{len(links)} de {len(trabalho['faltantes'])} anúncios cunhados")
if erro:
    print(f"ERRO: {erro}")
```

`gerar_links` é idempotente por (URL, etiqueta): repetir um anúncio que já tem
link devolve o mesmo link, sem duplicar. Se `erro` vier preenchido (sessão
expirada — HTTP 401/403 ou um lote inteiro com `total_success == 0`), o que já
foi cunhado **é gravado assim mesmo**; volte ao Passo 2 para recolher cookies
frescos e rode de novo só com o que faltou.

## Passo 4 — Conferir, limpar e commitar

1. `PYTHONPATH="$PWD/src" python -m afiliado.cli doctor` (ou `afiliado
   doctor`) deve mostrar `X de Y produto(s) do pool com anúncio linkado`.
2. Apague `data/_meli_anuncios.json`.
3. Mostre ao usuário: quantos anúncios foram cunhados, quantos produtos
   ficaram sem nenhum (esses não publicam — o programa recusa alguns
   vendedores, e em 2026-08-28 11 dos 64 produtos do pool foram podados por
   isso).
4. Commit: `chore: cunha links de anúncio do ML (N novos)` terminando com
   `Co-Authored-By:` do modelo em uso. `git add data/meli_links.json` com o
   caminho EXPLÍCITO. Se o repo tiver remote, pergunte antes de dar push.

## Notas

- **Quantos por produto e por quê:** 3. Medido em 2026-08-28 (53 produtos,
  1717 anúncios): 34 de 35 anúncios lidos em 26/08 ainda estavam na lista 2
  dias depois (97,1%, ~90% em 7 dias) — com 3 links a chance de os três
  sumirem numa semana é 0,09%, contra 10% com um só. E em 27 dos 52 produtos
  com anúncio elegível os 3 mais baratos JÁ SÃO a lista elegível inteira.
- **O piso de qualidade** (`anuncio_passa_no_piso`): novo E (Full OU loja
  oficial OU frete grátis). Ele existe porque em 12 dos 53 produtos o anúncio
  mais barato é um item barato com frete caro pago pelo comprador (R$ 8,00 +
  R$ 44,62 de frete). Custa 1 produto de 53 e +0,0% de preço na mediana.
- Produto sem nenhum anúncio linkado é descartado em silêncio pelo pipeline
  (a próxima oferta da fila assume) — rodar este skill quando o pool mudar
  evita que isso vire a norma. O run avisa uma vez por dia quando menos da
  metade do pool tem anúncio linkado.
- **Rotina mensal:** a sessão do painel expira e os anúncios envelhecem (~65%
  sobrevivem a 30 dias). Rode pelo menos uma vez por mês, e sempre depois de
  `/meli-pool-refresh`.
