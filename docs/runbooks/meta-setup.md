# Runbook — Setup Meta/Instagram (fase 2)

Checklist para habilitar o feed automático (`instagram_feed`). Faça na ordem;
os passos 1–3 são no celular/navegador, 4–6 no navegador. Tempo total: ~30 min.
Quando travar em qualquer passo, abra o Claude Code e peça ajuda citando o
número do passo — este arquivo é a referência compartilhada.

## 1. Conta do Instagram (business)

- [ ] Criar a conta do projeto no Instagram (ou usar a existente).
- [ ] Perfil → menu → **Configurações e privacidade** → **Tipo de conta** →
      **Mudar para conta profissional** → categoria **Empresa**.
- [ ] Preencher bio com o posicionamento ("achadinhos de autocuidado & casa")
      e o link do canal do Telegram como link da bio.

## 2. Página do Facebook

- [ ] Criar uma Página do Facebook com o mesmo nome do perfil
      (facebook.com/pages/create).
- [ ] Vincular: Instagram → **Configurações** → **Central de Contas** →
      adicionar a conta do Facebook → conectar a Página.
      (Alternativa: Página do FB → Configurações → Contas vinculadas → Instagram.)

## 3. App na Meta for Developers

- [ ] Acessar https://developers.facebook.com → **My Apps** → **Create App**.
- [ ] Tipo/caso de uso: **Business** (ou "Other" → Business). Nome: ex. "Afiliado Pipeline".
- [ ] O app fica em **modo de desenvolvimento** — para uso na própria conta
      (você é admin do app) NÃO precisa de App Review da Meta.

## 4. Token de acesso (Graph API Explorer)

- [ ] Abrir https://developers.facebook.com/tools/explorer/ e selecionar o app.
- [ ] Em **Permissions**, adicionar: `instagram_basic`,
      `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`,
      `business_management`.
- [ ] **Generate Access Token** → autorizar com sua conta, selecionando a
      Página e o Instagram do projeto.

## 5. Trocar por token de longa duração e pegar os IDs

No terminal (substitua os placeholders):

```bash
# 5a. Token de usuário de longa duração (60 dias)
curl "https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=<APP_ID>&client_secret=<APP_SECRET>&fb_exchange_token=<TOKEN_DO_EXPLORER>"

# 5b. Com o token acima: listar páginas → anote "id" da Página e o "access_token"
#     retornado aqui (token de PÁGINA derivado de token longo NÃO expira)
curl "https://graph.facebook.com/v21.0/me/accounts?access_token=<TOKEN_LONGO>"

# 5c. Com o id da Página: pegar o IG User ID
curl "https://graph.facebook.com/v21.0/<PAGE_ID>?fields=instagram_business_account&access_token=<TOKEN_DE_PAGINA>"
```

- [ ] Anotar: `IG_USER_ID` = `instagram_business_account.id` do passo 5c;
      `IG_ACCESS_TOKEN` = o access_token de Página do passo 5b.

## 6. Configurar e testar

- [ ] Definir as variáveis de ambiente `IG_USER_ID` e `IG_ACCESS_TOKEN`
      (local e nos GitHub Secrets).
- [ ] Teste rápido: `curl "https://graph.facebook.com/v21.0/<IG_USER_ID>?fields=username&access_token=<IG_ACCESS_TOKEN>"`
      deve retornar o username do perfil.
- [ ] Ligar o canal no `config.yaml`: `channels.instagram_feed: true`.
- [ ] Rodar `afiliado run --dry-run` e depois um run real para validar o
      primeiro post de feed.

## Notas

- O sticker de link em stories NÃO é suportado pela API oficial — os stories
  saem pelo fluxo semi-automático (arte + link chegam no seu chat de operações;
  você posta no app). Migração futura para automação total: estágio 2B.
- Tokens de Página derivados de token longo não expiram, mas invalidam se a
  senha mudar ou a sessão for revogada — se o feed parar com erro de token,
  refaça os passos 4–5.
