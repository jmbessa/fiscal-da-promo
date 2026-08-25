# Runbook — Setup Mercado Livre (fase 3, parte 1)

Checklist para habilitar a fonte de ofertas do Mercado Livre (`sources.meli`
em `config.yaml`, desligada por padrão). Cobre só **descoberta e
autenticação** — a geração automática de link de afiliado é um spike
pendente da parte 2 (ver seção final).

Quando travar em qualquer passo, abra o Claude Code e peça ajuda citando o
número do passo.

---

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
liberado, ele cai para `refresh_token`.

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

## 3. Configurar e ligar

- [ ] `.env` local e GitHub Secrets: `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`
      e, se aplicável (passo 2b), `MELI_REFRESH_TOKEN`.
- [ ] `config.yaml` → `sources.meli: true`.
- [ ] `afiliado doctor` → deve mostrar `✅ Mercado Livre: token ok; N
      ofertas`. Sem credenciais, mostra uma linha informativa (não falha o
      doctor).

## 4. Pool de links de afiliado (`data/meli_links.json`)

**Pendente do spike da parte 2:** não existe API oficial de geração de link
de afiliado no Mercado Livre — diferente da Shopee, cuja mutação GraphQL
gera o link automaticamente. Por ora, `resolve_affiliate_link` lê um pool
de links **pré-gerados manualmente**:

```json
{
  "MLB123456789": "https://mercadolivre.com/sec/abc123",
  "MLB987654321": "https://mercadolivre.com/sec/xyz789"
}
```

- [ ] No painel de afiliados do Mercado Livre (Mercado Livre Ads /
      programa de afiliados), gere o link curto para cada item que o
      pipeline for divulgar e adicione a entrada `<item_id>: <link>` em
      `data/meli_links.json` (o `item_id` é o `id` retornado pela busca,
      ex. `MLB123456789` — aparece no `doctor` e nos logs de descarte).
- [ ] Item sem entrada no pool é descartado silenciosamente pelo pipeline
      (comportamento já existente: promove a próxima oferta da fila) — não
      é um erro que derruba o run.
- [ ] Arquivo ausente ou inválido não quebra nada: o pool fica vazio e
      todo item do Mercado Livre é descartado até o arquivo ser criado.

## Notas

- `commission_pct` das ofertas do Mercado Livre vem de `meli.commission_pct`
  em `config.yaml` — uma **estimativa média** (padrão `4.0`, ou seja, 4%),
  não a taxa real por item: a busca pública não expõe comissão por item.
  **Ajuste esse valor com as taxas reais do seu painel de afiliados**
  (variam por categoria); um valor desatualizado ou zerado distorce o
  ranking por valor esperado (`ev_score`) e pode fazer o ML perder posição
  para a Shopee — ou, com `selection.min_ev_brl` ativo, ser descartado
  direto pelo piso. A parte 2 (spike do link de afiliado) deve reavaliar se
  dá para obter a taxa de comissão por item em vez da estimativa média.
- `rating` vem fixo em `0.0` — a busca não traz nota média.
- **Actions (`publish.yml`) é efêmero — cuidado com `refresh_token`:** cada
  execução do workflow começa do zero, sem `data/meli_token.json`
  persistido entre runs. Se a autenticação cair no fluxo `refresh_token`
  (passo 2b), a rotação se perde a cada execução e a autenticação quebra na
  seguinte. Nesse caso, a fonte do ML só deve rodar na VPS
  (`docs/runbooks/vps-setup.md`), onde `data/meli_token.json` sobrevive
  entre execuções — não no Actions. Com `client_credentials` funcionando
  (passo 2a, sem estado), o Actions serve normalmente.
