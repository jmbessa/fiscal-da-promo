# Runbook — Setup Mercado Livre (fase 3B — pool curado + preço ao vivo + links em lote)

Checklist para habilitar a fonte de ofertas do Mercado Livre (`sources.meli`
em `config.yaml`, desligada por padrão).

Quando travar em qualquer passo, abra o Claude Code e peça ajuda citando o
número do passo.

---

## 0. Como a fonte funciona (leia antes de mexer)

A descoberta de itens **não** usa mais a busca pública do ML — ela devolve
403 na API real. O fluxo é:

```
pool curado (data/meli_offers.json)
        │  fetch_offers: leitura local, sem rede
        ▼
   seleção/ranking (igual às outras fontes)
        │
        ▼
   refresh_price: GET /products/{id}/items — preço ao vivo, imediatamente
        │         antes de publicar. Descarta se o preço subiu além da
        │         mínima histórica (não é mais "oferta real").
        ▼
   resolve_affiliate_link: lê data/meli_links.json (pool de links)
```

### Endpoints confirmados contra a API real

- **Liberados** (`https://api.mercadolibre.com`, `Authorization: Bearer`):
  - `GET /highlights/MLB/category/{catId}` — usado na curadoria externa do
    pool (fora do escopo deste runbook; ver "Alimentando o pool" abaixo).
  - `GET /products/{productId}` — `permalink` vem **vazio**; a URL do
    produto é sempre montada como `https://www.mercadolivre.com.br/p/{id}`.
  - `GET /products/{productId}/items` — usado por `refresh_price`; traz o
    preço ao vivo por variação/vendedor. `original_price` quase sempre é
    `null` — não dá para calcular desconto por aqui, só o preço atual.
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

## 3. Pool curado (`data/meli_offers.json`)

`fetch_offers` só **lê** este arquivo — a curadoria (escolher quais produtos
entram, com que preço de referência e mínima histórica) é um processo
externo que grava o arquivo neste formato:

```json
{
  "generated_at": "2026-08-25",
  "valid_days": 30,
  "offers": [
    {"product_id": "MLB18725310", "title": "Creatina 1kg ...",
     "image_url": "https://http2.mlstatic.com/....jpg", "category": "MLB264586",
     "price_ref_cents": 6890, "price_historic_min_cents": 4792,
     "sales": 13337, "rating": 4.8}
  ]
}
```

- `price_ref_cents` — preço no momento da curadoria (vira `price_current_cents`
  inicial da oferta; é substituído pelo preço ao vivo em `refresh_price`
  logo antes de publicar).
- `price_historic_min_cents` — mínima histórica. **Obrigatório**: entrada sem
  ele (ou com valor não inteiro / <= 0) é pulada por `fetch_offers`, e a
  contagem das puladas entra no aviso do resumo do run. Vira
  `Offer.price_floor_cents` e alimenta o selo de menor preço.
- Desde a fase 4 o ML não tem teto de preço próprio: quem decide
  publicabilidade é `selection.max_above_ref` (não anunciar item mais caro que
  o típico) + `validate.check_price`, igual para as duas lojas. `price_ref_cents`
  vira `Offer.price_ref_cents`, a referência contra a qual o desconto é
  verificado (ver `afiliado.pricing`).
- Arquivo ausente, JSON inválido, ou vencido (`generated_at` + `valid_days`
  no passado) → `fetch_offers` devolve lista vazia **sem levantar exceção**;
  o pipeline segue normalmente só com as demais fontes, e acrescenta o aviso
  `ℹ️ meli: pool vazio ou vencido — rode /meli-links-refresh` no resumo do run.

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
- [ ] `data/meli_links.json` é público (links de afiliado, não segredo) e é
      commitado — só os **cookies da sessão** nunca são gravados em arquivo
      nem commitados (ver o skill).

## 5. Configurar e ligar

- [ ] `.env` local e GitHub Secrets: `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`
      e, se aplicável (passo 2b), `MELI_REFRESH_TOKEN`.
- [ ] `config.yaml` → `sources.meli: true`.
- [ ] `afiliado doctor` → deve mostrar `✅ Mercado Livre: token ok; N
      ofertas no pool`. Sem credenciais, mostra uma linha informativa (não
      falha o doctor). Pool vazio/vencido mostra `⚠️` com o motivo.

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
  (`price_ref_cents`/`sales`/`rating`), não de uma chamada ao vivo — refletem
  o momento da curadoria, não o instante da publicação (só o preço é
  atualizado ao vivo, via `refresh_price`).
- **Actions (`publish.yml`) é efêmero — cuidado com `refresh_token`:** cada
  execução do workflow começa do zero, sem `data/meli_token.json`
  persistido entre runs. Se a autenticação cair no fluxo `refresh_token`
  (passo 2b), a rotação se perde a cada execução e a autenticação quebra na
  seguinte. Nesse caso, a fonte do ML só deve rodar na VPS
  (`docs/runbooks/vps-setup.md`), onde `data/meli_token.json` sobrevive
  entre execuções — não no Actions. Com `client_credentials` funcionando
  (passo 2a, sem estado), o Actions serve normalmente.
