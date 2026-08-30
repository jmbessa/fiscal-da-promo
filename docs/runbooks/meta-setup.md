# Runbook — Setup Meta/Instagram (fase 2)

Checklist para habilitar os canais automáticos do Instagram (`instagram_feed`;
`instagram_story` desde a fase 5E; `instagram_reel` desde a 5T). Existem **duas
variantes** da API do Instagram; o pipeline suporta as duas (`instagram.api` no
`config.yaml`).

> **Este projeto roda a Variante B.** `config.yaml` traz
> `instagram.api: facebook_login` desde 2026-08-25 (token de Página
> permanente, já configurado). Vá direto para a **Variante B**, abaixo — a
> Variante A fica documentada para quem estiver começando do zero e não quiser
> criar Página do Facebook. Até a fase 5C este runbook recomendava a A
> enquanto o config rodava a B, e quem seguisse o texto gerava um token que o
> `graph.facebook.com` não aceita.

Quando travar em qualquer passo, abra o Claude Code e peça ajuda citando o
número do passo.

## Story pela API oficial (fase 5E) — a afirmação antiga estava ERRADA

Da fase 2A até a 5C este runbook, o spec e o `README` diziam que **"a API não
publica story"**, e por isso existia o `story_dispatch`: a arte ia para o chat
de operações e o dono postava à mão, 6 gestos por dia, 2.190 por ano.
**A premissa estava errada.** Em **2026-08-27** o fluxo foi testado AO VIVO na
conta real (`@ofiscaldapromo`, Variante B, token de Página permanente) e
publicou. O que foi medido, e que o canal `instagram_story` codifica:

1. **Container.** `POST /{ig_user_id}/media` com `image_url=<JPEG público>` e
   **`media_type=STORIES`** → `{"id": "18090130007292530"}`.
   **Sem `caption`** — story não aceita legenda pela API.
2. **Status.** `GET /{creation_id}?fields=status_code,status` devolve
   `IN_PROGRESS` logo após criar (`"Media is still being processed."`).
3. **Publicação.** `POST /{ig_user_id}/media_publish` com `creation_id`
   funcionou **mesmo com o container ainda `IN_PROGRESS`**. O canal faz polling
   assim mesmo (5 leituras, 1 s entre elas): com imagem maior, contar com isso é
   o tipo de coisa que falha em produção. Esgotadas as tentativas ainda em
   `IN_PROGRESS`, ele publica — foi o que funcionou ao vivo.
4. **Resultado.** `media_product_type: "STORY"`, `media_type: "IMAGE"`,
   `permalink: https://www.instagram.com/stories/ofiscaldapromo/<id>`.
5. **Cota.** `GET /{ig_user_id}/content_publishing_limit?fields=config,quota_usage`
   → `{"config": {"quota_total": 100, "quota_duration": 86400}, "quota_usage": 1}`.
   A cota é **compartilhada** entre feed e stories: 2 posts + 6 stories por dia
   usam 8 de 100.
6. **Sem sticker de link.** Não existe sticker de link pela API — em nenhuma das
   duas variantes. O story sai sem link clicável, e isso é aceito de propósito:
   a arte já traz o handle e a chamada, e a legenda do feed diz "link na bio e
   no canal do Telegram". **Não invente um sticker.**

`story_dispatch` continua no código e no `config.yaml`, DESLIGADO, como
fallback manual: se a conta perder `instagram_content_publish`, ligue-o e
desligue o `instagram_story` — o dia volta a sair, na mão.

## Reel pela API oficial (fase 5T) — o motor de aquisição que faltava

**Por que.** Em **2026-08-29**, medido na conta real pela Graph API:

| | |
|---|---|
| seguidores | **2** |
| posts | 5 (3 imagem, 2 carrossel) |
| alcance em 7 dias | **1 conta** |
| views em 7 dias | 2 |
| interações | **0** |
| posts do tipo REEL | **0** |

Conta nova tem alcance baixo — isso é esperado. Alcance **1 com cinco posts,
não**. O feed serve principalmente quem já segue; o Reel é o mecanismo que a
Meta usa para entregar a quem **não** segue. Sem ele o sistema publica para 2
pessoas por construção. A pesquisa do projeto tinha chegado ao mesmo lugar por
outro caminho (`docs/feed.md`): o Reel tem o maior *share rate* medido (0,10%)
e é "o motor de AQUISIÇÃO, que não existe: hoje a conta só tem o motor de
retenção".

**A peça.** É a arte de story (que já é 1080×1920) com o eixo do tempo por
cima: zoom lento da foto do produto (1,00 → 1,08) do primeiro ao último frame,
título entrando com fade e subida de 12 px, pill de preço crescendo de 0,9 a
1,0, meta e selo em fade. Tudo assenta em ~2,3 s; do 2,3 s em diante o frame é
a arte de story **idêntica, pixel a pixel**, com o zoom correndo — é isso que
faz o loop não ter costura. Nenhum layout novo foi inventado.

### O que foi MEDIDO nesta fase (2026-08-30, render local)

1. **Arquivo.** `1080×1920`, `8,00 s`, `24 fps`, **H.264 High / yuv420p**
   (`avc1`), mais uma faixa **AAC 48 kHz estéreo silenciosa**, e `+faststart`
   (o `moov` na frente, para a Meta não precisar baixar o arquivo todo antes
   de começar). A faixa silenciosa é SEGURO, não fato medido: a especificação
   de Reels da Meta lista AAC entre os requisitos de áudio e não diz o que
   acontece sem faixa nenhuma — custa 2 kb/s descobrir do jeito caro.
2. **Tamanho.** `0,19 MB` com foto real da Shopee (`0,13 MB` com foto
   sintética). O bot do Telegram, que é a hospedagem, baixa até **20 MB** —
   duas ordens de grandeza de folga. **Se algum dia passar de 20 MB, o defeito
   é do gerador**, não da hospedagem: o canal recusa antes de enviar, dizendo
   isso.
3. **Tempo de render.** `~3,5 s por peça` (2,5 s de frames + encode em
   paralelo), medido três vezes nesta máquina. O orçamento da fase era ~4 s a
   24 fps; ficou dentro, e por isso o projeto **não** caiu para 15 fps.
4. **Elegibilidade da aba Reels.** 9:16 ✅, entre 5 e 90 s ✅ (8 s), H.264 ✅.

### O que este canal manda à Graph API

Mesmos três passos do story (criar container → esperar `status_code` →
`media_publish`, medidos ao vivo em 2026-08-27, seção acima). O que muda:

1. `POST /{ig_user_id}/media` com **`media_type=REELS`** e **`video_url`** (não
   `image_url`);
2. **`caption`** — o Reel aceita legenda, o story não. Curta em cima (o Reel só
   mostra duas linhas sobre o vídeo) e com o bloco indexável embaixo: os posts
   do Instagram são lidos pelo Google desde 10/07/2025;
3. **`share_to_feed=true`** — o mesmo Reel também aparece na grade do perfil.
   Uma unidade de cota, duas superfícies;
4. **espera mais**: 15 leituras de `status_code` com 2 s entre elas (28 s no
   pior caso), contra as 5 de 1 s do story. Container de vídeo não é um JPEG —
   a Meta **transcodifica** o arquivo, e publicar antes do `FINISHED` é o que
   falha. Esgotadas as tentativas, ele publica assim mesmo, como o story: e aí
   o erro da Meta diz que o container não terminou, que é uma informação;
5. **a cota é PERGUNTADA, não chutada.** A documentação da Meta traz **100 e 50
   na mesma página**, e a janela é **MÓVEL** — ela libera 24 h depois de *cada*
   publicação, não à meia-noite. A fonte da verdade é
   `GET /{ig_user_id}/content_publishing_limit`, a mesma rota que o `doctor`
   imprime, e ela é a **primeira** chamada do publish: com a cota estourada não
   há por que pagar 3,5 s de ffmpeg e um upload de vídeo. Cota que não
   respondeu **não** é cota estourada — o canal segue, senão uma mudança de
   formato da Meta o calaria em silêncio.

**A hospedagem é a mesma da arte de imagem**: `sendVideo` ao chat de operações
e a URL do `getFile` como `video_url`, com o mesmo `ART_HOST_BOT_TOKEN` (A5) —
o token do bot administrador do canal público nunca vai à Meta. O Telegram às
vezes devolve `document` no lugar de `video`; a URL é a mesma e o canal aceita
as duas.

### O que NÃO foi medido, e é o que falta

**Nada foi publicado ao vivo.** Os passos acima são a extrapolação direta do
fluxo de story/carrossel que ESTE projeto mediu na conta real, mas nenhum
container `REELS` foi criado nesta conta ainda. O que a primeira publicação de
verdade vai responder, e que ninguém sabe hoje:

- se o container de vídeo termina dentro dos 28 s de polling (se não terminar,
  suba `InstagramReelChannel.max_polls`);
- se a Meta aceita o `.mp4` gerado sem reclamar de bitrate/keyframes;
- se `share_to_feed=true` se comporta como documentado nesta conta;
- se o Reel conta 1 ou mais unidades na cota de publicação.

### Ligar (depois de ver a peça)

- [ ] `pip install -e .[reel]` — ou tenha um `ffmpeg` no PATH. **Sem ffmpeg o
      canal não sobe**, o run avisa uma vez por dia e o resto do pipeline segue
      inteiro (é o molde do `playwright` da fase 5P).
- [ ] `afiliado run --dry-run` → ele grava **`.claude/previews/reel.mp4`** da
      primeira oferta que sairia e imprime tamanho, dimensão, duração e fps.
      **Abra o arquivo e olhe.** Cinco defeitos de desenho passaram
      despercebidos neste projeto por serem julgados no código — inclusive um
      botão que era elipse.
- [ ] Gostou: `channels.instagram_reel.enabled: true` no `config.yaml`. Ele
      **nasce desligado** de propósito.
- [ ] `max_per_day: 2` é o teto do PROJETO, e sai do MESMO balde da cota da
      Meta que o feed e o story: 2 posts + 2 Reels + N stories somam. Antes de
      subir qualquer um dos três, some os três.
- [ ] As envs são as mesmas dos outros dois canais do Instagram
      (`IG_USER_ID`, `IG_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`,
      `TELEGRAM_OPS_CHAT_ID`, e o `ART_HOST_BOT_TOKEN` da A5). Nada novo.

## Hospedagem da arte: use um bot secundário (fase 5C, A5)

A arte do feed **e a do story** é enviada ao chat de operações do Telegram e a
URL de `getFile` é o `image_url` que a Meta busca — e desde a 5T o **vídeo do
Reel** faz o mesmo caminho, por `sendVideo`, virando o `video_url`. **Essa URL
carrega o bot token** — e o que expira nela é o `file_path`, não o token.

- [ ] Crie um **segundo bot** no @BotFather (ex.: `@fiscalarte_bot`).
- [ ] Adicione-o SÓ ao chat de operações — ele não precisa (e não deve) ser
      administrador do canal público.
- [ ] `ART_HOST_BOT_TOKEN=<token do bot secundário>` no `.env` e nos GitHub
      Secrets.

Sem essa variável, quem hospeda a arte é o bot administrador do canal — e o
token dele vai à Meta em todo post. O run avisa uma vez por dia, **por canal**:
`⚠️ instagram_feed: arte hospedada pelo bot do canal — defina ART_HOST_BOT_TOKEN`
(e o mesmo com `instagram_story`).

---

## Variante A (alternativa) — "API do Instagram com Login do Instagram"

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

## Variante B (a que este projeto roda) — "API do Instagram com Login do Facebook"

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
- [ ] `config.yaml`: `instagram.api` conforme a variante,
      `channels.instagram_feed.enabled: true` e
      `channels.instagram_story.enabled: true` (os dois usam as MESMAS envs).
- [ ] `afiliado doctor` → deve mostrar `✅ Instagram: @ofiscaldapromo`.
- [ ] `afiliado run --dry-run` e depois um run real para validar o primeiro post.

## Notas
- O sticker de link em stories NÃO é suportado por nenhuma das variantes. O que
  NÃO se conclui disso — e o projeto concluiu errado até a fase 5C — é que o
  story não possa ser publicado: ele pode, só sai sem link clicável (ver a
  seção "Story pela API oficial", acima).
- A cota de publicação (100/24h) é **compartilhada** entre feed e stories.
- A API só aceita **JPEG** para `image_url`; os canais convertem a arte
  automaticamente e a hospedam temporariamente via Telegram.
