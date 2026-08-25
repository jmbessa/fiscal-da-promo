# Runbook — Setup Meta/Instagram (fase 2)

Checklist para habilitar o feed automático (`instagram_feed`). Existem **duas
variantes** da API do Instagram; o pipeline suporta as duas (`instagram.api` no
`config.yaml`). Use a **Variante A** — é a mais simples: o token sai direto do
painel da Meta, sem Página do Facebook e sem Graph API Explorer.

Quando travar em qualquer passo, abra o Claude Code e peça ajuda citando o
número do passo.

---

## Variante A (recomendada) — "API do Instagram com Login do Instagram"

`config.yaml` → `instagram.api: instagram_login` (já é o padrão).

### A1. Conta do Instagram (business ou creator)
- [ ] Criar/usar a conta `@ofiscaldapromo` e converter para **profissional**
      (Configurações → Tipo de conta → Empresa **ou** Criador de conteúdo).
- [ ] Bio com o posicionamento e o link `t.me/fiscaldapromo` (texto pronto em
      `docs/brand-guidelines.md`).
- Não precisa de Página do Facebook.

### A2. App na Meta for Developers
- [ ] https://developers.facebook.com → **My Apps** → **Create App**.
- [ ] Na criação, quando perguntar o caso de uso, escolha o que menciona
      **Instagram** (ex.: "Gerenciar mensagens e conteúdo no Instagram" /
      "Instagram") — se não aparecer, escolha **Outro** e, na tela seguinte,
      tipo **Business**. Nome: ex. "Fiscal da Promo".
- [ ] Se o app já existir e NÃO for do tipo Business, crie outro: a Meta não
      permite trocar o tipo depois.
- [ ] No painel do app, menu lateral → **Instagram** → **"API setup with
      Instagram business login"** (ou "Configuração da API com login de
      empresa do Instagram"). Se o menu "Instagram" não existir, vá em
      **Adicionar produto** → **Instagram** → Configurar.
- [ ] O app fica em **modo de desenvolvimento** — funciona para a sua própria
      conta sem App Review.

### A3. Vincular a conta e gerar o token (tudo no painel)
- [ ] Na tela "API setup with Instagram business login", seção
      **Generate access tokens** → **Add account** → faça login no Instagram
      com a conta do projeto e autorize.
- [ ] Ao lado da conta, clique **Generate token** → autorize as permissões
      `instagram_business_basic` e `instagram_business_content_publish`
      (e `instagram_business_manage_comments`, opcional) → copie o token.
      Ele é **de longa duração (60 dias)**.
- [ ] Anote também o **Instagram app ID** e **Instagram app secret** mostrados
      nessa tela (necessários apenas para renovar/trocar tokens no futuro).

### A4. Pegar o IG_USER_ID e testar
```bash
curl "https://graph.instagram.com/v21.0/me?fields=user_id,username&access_token=<TOKEN>"
```
- [ ] `IG_USER_ID` = o campo `user_id` (ou `id`) da resposta; confira que
      `username` é o do projeto.
- [ ] Teste de publicação (limite oficial: 100 posts/24h, só JPEG — o pipeline
      já converte a arte):
```bash
curl "https://graph.instagram.com/v21.0/<IG_USER_ID>/content_publishing_limit?access_token=<TOKEN>"
```

### A5. Renovação do token (a cada ~50 dias)
```bash
curl "https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<TOKEN_ATUAL>"
```
Retorna um token novo válido por mais 60 dias — atualize `.env` e o secret
`IG_ACCESS_TOKEN` no GitHub. (Automatizar essa renovação está no backlog.)

---

## Variante B — "API do Instagram com Login do Facebook"

`config.yaml` → `instagram.api: facebook_login`. Exige conta business
**vinculada a uma Página do Facebook**; escopos `instagram_basic`,
`instagram_content_publish`, `pages_show_list`, `pages_read_engagement`,
`business_management`; host `graph.facebook.com`.

- [ ] Página do Facebook vinculada ao Instagram (Central de Contas).
- [ ] App tipo Business com o produto **Facebook Login for Business** e o
      produto **Instagram** adicionados — só então `instagram_content_publish`
      aparece no Graph API Explorer.
- [ ] Graph API Explorer → permissões acima → **Generate Access Token**.
- [ ] Trocar por token longo e obter IDs:
```bash
curl "https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=<APP_ID>&client_secret=<APP_SECRET>&fb_exchange_token=<TOKEN_DO_EXPLORER>"
curl "https://graph.facebook.com/v21.0/me/accounts?access_token=<TOKEN_LONGO>"
curl "https://graph.facebook.com/v21.0/<PAGE_ID>?fields=instagram_business_account&access_token=<TOKEN_DE_PAGINA>"
```
- [ ] `IG_USER_ID` = `instagram_business_account.id`; `IG_ACCESS_TOKEN` =
      token de Página (derivado de token longo, não expira).

---

## Configurar e ligar (as duas variantes)

- [ ] `.env` local e GitHub Secrets: `IG_USER_ID`, `IG_ACCESS_TOKEN`.
- [ ] `config.yaml`: `instagram.api` conforme a variante e
      `channels.instagram_feed.enabled: true`.
- [ ] `afiliado doctor` → deve mostrar `✅ Instagram: @ofiscaldapromo`.
- [ ] `afiliado run --dry-run` e depois um run real para validar o primeiro post.

## Notas
- O sticker de link em stories NÃO é suportado por nenhuma das variantes — os
  stories seguem pelo fluxo semi-automático (`story_dispatch`).
- A API só aceita **JPEG** para `image_url`; o canal converte a arte
  automaticamente e a hospeda temporariamente via Telegram.
