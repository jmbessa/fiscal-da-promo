# Sticker de link em Stories do Instagram, de forma programática — levantamento 2025–2026

**Conta alvo:** `@ofiscaldapromo` — Instagram Business vinculado a Página do Facebook, publicando por `graph.facebook.com/v21.0`, App ID 1339423115070899, com `instagram_content_publish`.
**Uso:** pipeline de afiliados, ~6 stories/dia, cada um precisando levar a um link curto Shopee / Mercado Livre.
**Data da pesquisa:** 2026-08-27. Todas as URLs foram acessadas nesta data salvo indicação.

---

## Legenda de força de evidência

| Marca | Significado |
|---|---|
| **FORTE** | Documentação oficial da Meta, código/issue do próprio mantenedor, resposta de API, ou página primária do fornecedor |
| **MÉDIA** | Fontes secundárias datadas e mutuamente consistentes (parceiros Meta, help centers de terceiros, imprensa técnica) |
| **ANEDÓTICA** | Relato isolado, fórum, blog de SEO sem fonte, ou inferência minha |

---

## Resposta curta (antes dos detalhes)

**Não existe nenhuma forma oficial e documentada de publicar um Story com sticker de link clicável pela Graph API.** A Meta diz isso literalmente, na página de referência do endpoint que você já usa. Isso é **FORTE** e não muda com versão de API, com permissão adicional, nem com a variante Instagram Login.

Existem exatamente **cinco** caminhos que produzem um sticker clicável sem alguém tocar na tela, e todos têm um custo:

1. **Meta Business Suite** (produto de primeira parte da própria Meta) — aparentemente *consegue* agendar Story com sticker de link, mas isso é **contestado** e precisa de 5 minutos de verificação manual sua. Se confirmar, é o melhor caminho de longe.
2. **Anúncio de Stories** pela Marketing API — o CTA é clicável, mas é anúncio pago e **não** aparece no seu story orgânico para seus seguidores.
3. **API privada** (instagrapi self-hosted) — funciona, e a conta é a moeda de risco.
4. **Storrito** — instagrapi terceirizado com marca; pede sua senha do Instagram. Mesmo risco, US$19/mês.
5. **Automação de dispositivo** (Appium/ADB em celular real) — o artefato publicado é 100% nativo; o risco migra do conteúdo para o fingerprint do aparelho.

E há o caminho de risco zero que você já tem funcionando: story pela Graph API **sem** sticker, empurrando para link na bio / Telegram, ao custo de ~2–4x em cliques.

---

## 1. Graph API oficial — existe algum suporte a sticker?

### 1.1 A frase que encerra a questão

Na referência do endpoint **IG User `media`** (o mesmo `POST /{ig-user-id}/media` que você já validou), na seção *Story Limitations*:

> "Publishing stickers (i.e., link, poll, location) is not supported; however mentioning users without a sticker is supported."

Fonte: <https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media/> (acessado 2026-08-27). — **FORTE**

Isso cobre nominalmente **link**, **poll** e **location**. Não é omissão de documentação: é uma negativa explícita.

### 1.2 Lista completa de parâmetros de `POST /{ig-user-id}/media` (mesma fonte, **FORTE**)

Citações literais da tabela de parâmetros:

| Parâmetro | Texto oficial | Vale para STORIES? |
|---|---|---|
| `media_type` | "Required for carousels, stories, and reels. Indicates container is for a carousel, story or reel. Value can be: CAROUSEL, REELS, STORIES" | sim |
| `image_url` | "For images only and required for images. The path to the image. We will cURL the image using the URL that you specify so the image must be on a public server." | sim (é o que você usa) |
| `video_url` | "Required for videos and reels. Applies only to videos and reels. Path to the video." | sim |
| `user_tags` | "Required for user tagging in images, videos, and stories. An array of public usernames and x/y coordinates…" | **sim, desde 2025-07-09** |
| `product_tags` | "Required for product tagging. **Applies only to images and videos.** An array of objects specifying which product tags to tag the image or video with (maximum of 5…)" | **não** |
| `caption` | "A caption for the image, video, or carousel." | **não** — bate com o que você observou ao vivo |
| `collaborators` | "For Feed image, Reels and Carousels only. … **Not supported for Stories.**" | **não** |
| `location_id` | "The ID of a Page associated with a location…" | não citado para stories; e `location` está na lista de stickers proibidos |

**Campos que você pediu para eu procurar e que NÃO EXISTEM em lugar nenhum da referência:** `link_attachment`, `sticker`, `story_sticker`, `link_sticker`, `swipe_up`, qualquer parâmetro contendo "link" ou "sticker" para criação de container. — **FORTE** (ausência verificada na tabela completa)

Nota de data oficial: *"On July 9, 2025, we added support for the existing `user_tags` field for image and video stories on the `/<IG_ID>/media` endpoint."* — **FORTE**

### 1.3 Limitações gerais do guia de Content Publishing (**FORTE**)

<https://developers.facebook.com/docs/instagram-platform/content-publishing/>

> "JPEG is the only image format supported. Extended JPEG formats such as MPO and JPS are not supported."
> "Shopping tags are not supported."
> "Filters are not supported."

E sobre stories, o guia inteiro tem **uma frase** (a seção "Story posts"):

> "To publish a reel, create a container for the media object and include the `media_type` parameter set to `STORIES`."

(sim, o texto oficial diz "reel" ali — é um erro de copy da própria Meta.)

Rate limit oficial, que bate com o seu `content_publishing_limit`:

> "Instagram accounts are limited to 100 API-published posts within a 24-hour moving period. Carousels count as a single post."

— **FORTE**. 6 stories/dia consome 6% da cota; a cota não é o gargalo.

### 1.4 O changelog 2024–2026: a Meta constrói *leitura* de sticker de link, nunca *escrita*

<https://developers.facebook.com/docs/instagram-platform/changelog> — **FORTE**

Duas entradas são muito reveladoras:

- **2026-06-22 — Story Insights:** nova métrica `link_clicks` no IG Media Insights para Stories, medindo toques em stickers de link. Disponível **apenas com Facebook Login**.
  Confirmado na referência de insights: `link_clicks` = *"The number of taps on links in your story."* — **FORTE**
- **2025-12-12 — Webhooks:** o webhook passa a incluir o campo `link_sticker_url` quando alguém responde a um story por Direct.

Ou seja: em 2025–2026 a Meta adicionou **duas** superfícies novas para *ler* dados de sticker de link, e **zero** para *criar* um. Isso não parece backlog esquecido; parece decisão de produto. — inferência **MÉDIA**, mas apoiada em dois fatos **FORTES**.

Nenhuma outra entrada do changelog 2024–2026 toca em publicação de stickers. O que entrou em content publishing no período foi: `trial_params` para Trial Reels (2025-12-03), `is_ai_generated` (2026-06-22), rótulo de publi via `branded_content_sponsor_ids`/`is_paid_partnership` (2026-04-22), Instagram Audio API para Reels (2026-06-01), e um endpoint de deletar mídia incl. stories (2025-12-03, permissão `instagram_manage_contents`).

### 1.5 A variante Instagram Login (`graph.instagram.com`) é diferente?

Não, e na verdade é **pior** para você:

- O guia de content publishing do Instagram Login descreve o mesmo `media_type=STORIES` e não menciona nenhum sticker/link. — **FORTE** (<https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing>)
- A métrica `link_clicks` de story é **Facebook Login only** (changelog 2026-06-22). — **FORTE**

Você já está no lado certo (Facebook Login + Página). Migrar para `graph.instagram.com` só te faria perder a métrica. **Não migre.**

### 1.6 Existe campo não documentado?

Não encontrei nenhuma demonstração pública de um campo oculto que funcione. O sinal mais próximo é negativo: o pedido de feature no Postiz (agendador open-source), issue [#580 aberta em 2025-01-28](https://github.com/gitroomhq/postiz-app/issues/580), *"Add the possibility to add the link sticker on the Instagram Story"*, foi **fechada como "not planned"** — os desenvolvedores não acharam mecanismo. — **MÉDIA**

**Marcação honesta:** eu não consigo provar ausência de campo não documentado. Só posso dizer que (a) a documentação nega explicitamente, (b) nenhum agendador do mercado inteiro que usa API oficial consegue fazer, e (c) ninguém publicou um método. Se existisse um campo secreto, alguém do tamanho da Later/Buffer/Sprout já estaria usando. Considero a probabilidade de existir **baixa**, e **não vale gastar tempo fuzzando o endpoint** — chamadas malformadas em volume são exatamente o tipo de sinal que faz a Meta revisar um app.

---

## 2. Marketing / Ads API — dá para fazer um story com link "parecendo orgânico"?

### 2.1 O que dá para fazer

- **Anúncio em placement Stories:** sim. No ad set, `targeting.instagram_positions` aceita `story`. O criativo de Stories tem um CTA (botão/sticker "Saiba mais") que é clicável e leva ao `link_url`. — **MÉDIA** (documentação oficial de ad creative não abriu pra mim; corroborado por múltiplas fontes técnicas e pelo blog de devs da própria Meta sobre IG Story Ads, <https://developers.facebook.com/ads/blog/post/v2/2017/03/01/ig-story/>)
- **Reaproveitar um story orgânico ativo como anúncio:** existe `source_instagram_media_id` no ad creative, que aceita posts de feed e **stories ativos**. Alternativa: montar `object_story_spec` direto. — **MÉDIA**
- **Dark post / unpublished:** sim, é o modo padrão de um anúncio criado no Ads Manager/API — o criativo não aparece no perfil nem no feed orgânico. A Meta chama de *unpublished post*. — **MÉDIA**

### 2.2 O que NÃO dá — e isso é decisivo

Um anúncio de Stories **não** é um story orgânico. Ele:

- **não** entra no anel de story do seu perfil para seus seguidores;
- é entregue a uma audiência paga, marcado como "Patrocinado";
- passa por revisão de anúncio (link de afiliado + landing de marketplace é categoria historicamente sensível);
- exige conta de anúncios, método de pagamento e, no Brasil, tributação sobre a mídia.

Ou seja: **isso não substitui os 6 stories/dia.** É outro canal, com outra economia. — inferência **MÉDIA**, direta da natureza do produto.

### 2.3 Quanto custaria rodar 6/dia como anúncio

Mínimos oficiais da Meta: **US$1/dia** por ad set faturado por impressão; **US$5/dia** por ad set otimizado para cliques/conversões/eventos de baixa frequência. — **MÉDIA** (fontes secundárias consistentes; a página oficial de help da Meta não abre para fetch)

CPM de Stories: ~US$4,50 global no 2T/2026; **Brasil é dos mercados mais baratos, com CPM podendo ficar abaixo de US$1**. — **MÉDIA**

Cenários (câmbio assumido ~R$5,40/US$ — **confira antes de usar**):

| Arranjo | Custo/dia | Custo/mês |
|---|---|---|
| 6 ad sets separados no mínimo de impressão (US$1 cada) | US$6 ≈ R$32 | ≈ **R$975** |
| 1 ad set com 6 criativos, otimizado por clique (US$5) | US$5 ≈ R$27 | ≈ **R$810** |
| Piso realista para não ficar preso em learning phase | US$10–40 | **R$1.600–R$6.500** |

**Leitura:** o piso técnico (~R$800–1.000/mês) é factível, mas você compra alcance pago mínimo — a US$1/dia num CPM de US$1 são ~1.000 impressões/dia divididas por 6 criativos. Provavelmente rende menos que os stories orgânicos que você já publica de graça. — inferência **MÉDIA**.

### 2.4 Regras dos programas de afiliado sobre tráfego pago

- **Shopee Brasil (primária, help center):** proibido "anúncios pagos em search ou shopping, como Google Ads e Bing Ads para promover links de afiliados"; **permitido** "para Instagram, TikTok, Facebook, Pinterest e YouTube você pode impulsionar publicações pagas, **desde que seja feito a partir da sua rede social**" — a mesma cadastrada no programa. — **FORTE** ([help.shopee.com.br art. 170923](https://help.shopee.com.br/10/article/170923-%5BFAQ%5D-%C3%89-permitido-fazer-tr%C3%A1fego-pago-no-Facebook-ou-Instagram), sem data no artigo)
  → Impulsionar do próprio `@ofiscaldapromo` está dentro das regras. Dark post de uma conta que não é a cadastrada, **não**.
- **Mercado Livre:** fontes secundárias brasileiras se contradizem — umas dizem que tráfego pago é permitido menos em buscadores, outras que é proibido apontar anúncio pago direto para o domínio do ML. — **ANEDÓTICA**. **Lacuna de evidência: leia os T&C do programa de afiliados do ML antes de gastar um real em mídia com link do ML.**

---

## 3. Bibliotecas de API privada

### 3.1 `instagrapi` (subzeroid/instagrapi) — estado em 2026-08-27

**Vivíssimo.** Dados da API do GitHub e do PyPI, coletados hoje: — **FORTE**

- Último push: **2026-08-26 10:54 UTC** (ontem)
- Última release: **2.18.18**, publicada **2026-08-26**
- Releases nas últimas 2 semanas: 2.18.13 → 2.18.18 (seis)
- 6.715 estrelas, **1 issue aberta**, não arquivado

Um repositório com 2.800+ commits e uma issue aberta significa triagem agressiva — o mantenedor fecha rápido. Não interprete "1 issue aberta" como "sem bugs".

### 3.2 Suporte a sticker de link: existe, e a API é essa

Assinatura oficial documentada — **FORTE** (<https://subzeroid.github.io/instagrapi/usage-guide/story.html>):

```
photo_upload_to_story(path: Path, caption: str = "", upload_id: str = "",
    mentions: List[StoryMention] = [], locations: List[StoryLocation] = [],
    links: List[StoryLink] = [], hashtags: List[StoryHashtag] = [],
    stickers: List[StorySticker] = [], medias: List[StoryMedia] = [],
    polls: List[StoryPoll] = [], extra_data: Dict[str, str] = {},
    resize_mode: StoryResizeMode = "fill")
```

`video_upload_to_story` tem a mesma superfície. Uso: `StoryLink(webUri="https://…")`. A doc registra que *"isto não é mais o antigo fluxo de 'swipe up' do Instagram"* — é o sticker moderno.

### 3.3 A história de quebra — e por que ela importa mais que o suporte

Isto é a evidência mais útil do relatório inteiro sobre fragilidade. — **FORTE** (issue primária + comentário do mantenedor)

- **2025-11-03** — issue [#2320](https://github.com/subzeroid/instagrapi/issues/2320), *"[BUG] Instagram post story with link sticker is not working"*: o story publica, mas **o link e a hashtag simplesmente não aparecem**.
- **2025-11-06** — outro usuário: *"Has anyone already solved this problem? I need this function."*
- **2026-04-16** — o mantenedor (`subzeroid`) fecha, com detalhe técnico:

  > "Fixed on current master. This turned out to be a two-part issue:
  > 1. Story upload/configure needed to keep all sticker ids instead of collapsing them to the first one. Commit: e4c3820
  > 2. Story extraction also needed to read links from `story_link_stickers[*].story_link`, not only from `story_cta`.
  > […] live Docker smoke with a real test account: uploaded a temporary story with link + hashtag, fetched it back via `story_info()`, confirmed parsed links and hashtags were present, then deleted the story"

**Leia a data duas vezes: o sticker de link ficou quebrado por ~5,5 meses e falhava em silêncio** — o story ia ao ar sem link, e você só descobriria olhando. Para um pipeline de afiliados, "publicou mas sem link" é o pior modo de falha possível: gasta a cota, gasta a atenção do seguidor, e não converte nada.

**Implicação de engenharia, se você for por aqui:** obrigatório verificar após cada publicação com `story_info()` e conferir que `links` voltou preenchido; se não voltou, alertar. Não confie no retorno do upload.

### 3.4 Autenticação, sessão e 2FA — **FORTE** (doc oficial)

- Precisa de **usuário + senha** ao menos uma vez: `login(username, password)`.
- Depois dá para reusar: `dump_settings()` / carregar settings. A doc recomenda explicitamente isso:
  > "A browser/web `sessionid` can be rejected with `login_required` or invalidated server-side; for long-lived automation, prefer `login()` once, then `dump_settings()` and reuse the saved settings."
- **2FA:** só **TOTP** (Google Authenticator e similares) via `login(username, password, verification_code)`. **Não funciona com SMS.** Se `@ofiscaldapromo` estiver com 2FA por SMS, teria de migrar para app autenticador.
- Avisos operacionais da própria doc: *"Use one stable device profile and one stable IP (or subnet) per account whenever possible"*; alinhar proxy, locale, country code e timezone — descasamento aumenta detecção e bloqueio.

### 3.5 Risco real de ban — o que dá e o que não dá para afirmar

**O que o próprio mantenedor escreve no README** (isto é o mais forte que existe) — **FORTE**:

> "Private API automation is fragile in production because account trust, proxies, device state, challenges, and rate limits can change independently."

> "the instagrapi project is best suited for **testing, research, and controlled internal automation**"

E ele encaminha produção para um SaaS pago: *"For production private API infrastructure, a hosted provider such as HikerAPI may be a better fit than maintaining accounts, proxies, and challenge handling yourself."*

**Tradução:** o autor da biblioteca está te dizendo para não colocar isso em produção com uma conta que importa.

**Relatos concretos no issue tracker** (busca na API do GitHub, **FORTE** como existência, mas **não quantificam taxa**):

| # | Data | Título |
|---|---|---|
| 581 | 2022-02-18 | "instagram banned my account" |
| 804 | 2022-07-23 | "[BUG] Block account" |
| 1155 | 2023-02-23 | "[BUG] When using `user_follow` method, instagram blocks account forever" |
| 1470 | 2023-07-19 | conta desabilitada após pedidos repetidos de código por e-mail |
| 1806 | 2024-02-19 | "[BUG] instagram suspends the account even after paid proxy use" |
| 2376 | 2026-02-11 | "[BUG] cl.login Not working" |
| 2718 | 2026-07-04 | "[BUG] challenge_required / Can't login" |
| 2737 | 2026-07-13 | "Raise `AccountSuspended` for suspended challenges" |

**Padrão que eu leio nisso** (inferência **MÉDIA**): quase todo relato de *banimento* vem de ações de **engajamento em massa** — follow/unfollow, scraping, signup automatizado, DM. Relatos de banimento por *só publicar* são raros. Mas os relatos de **`challenge_required` no login** são recentes e recorrentes (jul/2026), e em julho de 2026 o projeto adicionou uma exceção dedicada `AccountSuspended` — você não cria um tipo de erro para algo que acontece uma vez por ano.

**Sobre os números que circulam:** blogs de 2026 repetem "menos de 0,5%/ano com API oficial vs 15–30%/ano com automação de browser". **Isso é conteúdo de marketing sem fonte primária. Não use esse número para decidir nada.** — **ANEDÓTICA**, marcada como tal deliberadamente.

**Não existe taxa de ban publicada e confiável para contas Business que só publicam stories via instagrapi.** É uma lacuna real de evidência e eu não vou inventar um número.

### 3.6 Termos — o que exatamente é violado

- **Instagram Terms of Use:** *"You can't attempt to create accounts or access or collect information in unauthorized ways. This includes creating accounts or collecting information in an automated way without our express permission."* — **MÉDIA** (a página do help center é renderizada por JS e não abre para fetch; o texto é consistentemente citado por múltiplas fontes)
- **Meta Terms of Service** (em vigor desde 2025-01-01): *"You may not access or collect data from our Products using automated means … without our prior permission … **regardless of whether such automated access or collection is undertaken while logged-in to a Facebook account**."* — **MÉDIA** (<https://www.facebook.com/terms>)

Nota de precisão: essas cláusulas falam de **coletar dados**, não de publicar. O problema jurídico/técnico mais nítido do instagrapi não é essa cláusula e sim que ele **se faz passar pelo app móvel oficial** (assina requisições como cliente Instagram) — isso é "acesso por meio não autorizado" sem ambiguidade.

### 3.7 Alternativas

- **`ping/instagram-private-api`** (JS) e **`ping/instagram_private_api`** (Python): a issue [#166 "Add Story Sticker Support"](https://github.com/ping/instagram_private_api/issues/166) segue como pedido; o projeto está muito menos ativo que o instagrapi. Não recomendo. — **MÉDIA**
- **HikerAPI** (SaaS do mesmo autor): terceiriza contas/proxies/challenges. Move o risco operacional, não elimina o risco da *sua* conta se for a sua sessão. — **MÉDIA**

---

## 4. Agendadores comerciais — algum realmente automatiza o sticker?

### 4.1 O consenso: não, todos caem em "publicação por notificação"

| Ferramenta | Story com link automático? | Citação |
|---|---|---|
| **Buffer** | Não | Tabela oficial: "Story Link — **Only if scheduled as a notification**"; idem "Story Text" e "Music". — **FORTE** ([support.buffer.com/article/657](https://support.buffer.com/article/657-scheduling-instagram-posts-and-reels)) |
| **Ayrshare** (API-first) | Não | Cita a Meta ao pé da letra: *"Publishing stickers (i.e., link, poll, location) is not supported by Instagram."* Também: *"Instagram Stories do not support post text"*; stories só em **Business Account**, não Creator. — **FORTE** ([ayrshare.com/docs](https://www.ayrshare.com/docs/apis/post/social-networks/instagram)) |
| **Vista Social** | Não | *"Meta's API doesn't currently allow 3rd-party tools … to auto-publish Stories with interactive elements like stickers or links."* — **MÉDIA** (403 no fetch direto; via índice de busca) |
| **Later** | Não | Página "Instagram Publishing Restrictions in Later" documenta a limitação; notificação para stickers. — **MÉDIA** (403 no fetch) |
| **Metricool** | Não | Stories com links/menções/stickers exigem publicação manual via notificação (push ou e-mail). — **MÉDIA** |
| **Sprout Social** | Não | *"If you use a third-party tool like Sprout Social, you must use the **Mobile Publisher** workflow, where you get a notification on your phone, to add any interactive stickers, including Music, Polls, Links or Questions, before posting."* — **MÉDIA** ([sproutsocial.com, 2026-02-02](https://sproutsocial.com/insights/schedule-instagram-stories/)) |
| **SocialPilot** | Não | Mesmo modelo de reminder móvel. — **MÉDIA** |
| **mLabs** 🇧🇷 | Não | Tem modo dedicado **"Stories notificado"**: você recebe notificação no app da mLabs no horário e finaliza no Instagram. — **MÉDIA** ([ajuda.mlabs.com.br art. 7942682](https://ajuda.mlabs.com.br/pt-BR/articles/7942682-como-agendar-stories-notificado-do-instagram-pela-mlabs)) |
| **Etus** 🇧🇷 | Não encontrei evidência de sticker automático | — **ANEDÓTICA** (só comparativos genéricos) |

**Conclusão:** nenhum agendador que usa API oficial automatiza o sticker. Isso é uma **confirmação independente e do tamanho do mercado inteiro** de que não existe endpoint escondido. — **FORTE por convergência**

### 4.2 A exceção comercial: Storrito — e ela revela exatamente o que você suspeitava

Storrito se vende como *"the only story scheduler on the market that allows you to schedule your Instagram Stories with a link sticker"*, publicando **automaticamente** (não por notificação). — **FORTE** (páginas do próprio fornecedor)

**Como ele se conecta — este é o detalhe que entrega o jogo:**

- A página de conexão instrui a **"enter your Instagram username and password"**. — **FORTE**
- A página de preços diz: **"No annoying confirmations and no business account required"**. — **FORTE**

Conta Business não é obrigatória ⇒ **não é Graph API** (a Graph API exige Business/Creator). Senha ⇒ **é login de API privada**. Storrito é, funcionalmente, **instagrapi hospedado com marca**.

- **Preço:** **US$19/mês por conta do Instagram** (créditos reembolsáveis; stories e reels ilimitados; cross-post para Facebook incluso; US$2 de crédito de teste sem cartão). ≈ **R$103/mês** ao câmbio ~R$5,40. — **FORTE** (preço), câmbio a confirmar.
- **Tem API própria:** HTTP/JSON com Bearer token, endpoints `list-instagram-users` e `schedule-instagram-story`, base URL por conta. Ou seja: dá para plugar no seu pipeline sem interface. — **FORTE** (<https://storrito.com/help-center/storrito-api/>)
- **Divulgação de risco:** **nenhuma**. As páginas não mencionam banimento, termos do Instagram, nem risco de conta. — **FORTE** (ausência verificada)

**Veredito:** Storrito **não revela um caminho oficial que estamos perdendo**. Ele confirma que não existe. O que ele oferece é *terceirização do risco operacional* (proxies, device state, challenges, e o trabalho de consertar quando a Meta muda o protocolo — vide os 5,5 meses de quebra do instagrapi), mantendo **o mesmo risco de conta**, porque a conta continua sendo a sua e a senha vai para um terceiro.

### 4.3 A exceção que importa de verdade: **Meta Business Suite**

Aqui há um achado forte o suficiente para mudar a recomendação — e **contestado** o suficiente para eu não afirmar sozinho.

**A favor (3 fontes, datadas, uma delas Meta Business Partner):**

- **Sprout Social, 2026-02-02:** *"Unlike third-party tools, **Meta Business Suite allows you to schedule links natively without needing a mobile workflow**."* — **MÉDIA**
- **SocialPilot, 2026-08-04:** *"**Meta Business Suite can schedule a Story with a link sticker**, but music stickers, polls, and quiz stickers aren't supported through scheduled publishing."* — **MÉDIA**
- Terceira fonte de 2026: *"Meta Business Suite lets you schedule Stories natively and publish them automatically, though only one at a time and without music, poll, or quiz stickers"* — note que a lista de exclusões **não inclui link**. — **MÉDIA**
- Limites relatados: agendamento até **~29 dias** à frente, mínimo **20 minutos**, **um story por vez**. — **MÉDIA**
- Existe página oficial da Meta com o título **"Sobre a figurinha de link para o Instagram Stories | Central de Ajuda da Meta para Empresas"** (<https://www.facebook.com/business/help/529979981436890>) e **"Use creative features in Instagram Stories | Meta Business Help Centre"** (<https://www.facebook.com/business/help/520244998527406>). — **FORTE que existem**, mas **não consegui ler o conteúdo**: as páginas de help da Meta são renderizadas por JavaScript e voltam só o título.

**Contra (2 fontes, uma brasileira):**

- Zeely.ai (2026) e portalinsights.com.br: o Meta Business Suite **não** permite adicionar link em story agendado; só imagens/vídeos simples, sem enquete, link ou sticker interativo. — **MÉDIA**

**Como resolver isto em 5 minutos — e é a coisa mais valiosa deste relatório inteiro:**

> Abra `business.facebook.com` logado como `@ofiscaldapromo` → **Criar story** (ou Planejador → Criar → Story) → suba uma imagem → procure a bandeja de figurinhas → veja se existe a figurinha **"Link"**. Se existir, cole uma URL e agende para +25 minutos.
>
> Se o story sair no ar com sticker clicável **sem você tocar no celular**, está resolvido: existe caminho de primeira parte da Meta, e o restante deste documento vira plano B.

**Por que isso mudaria tudo:** se o MBS faz isso, dá para dirigir o MBS por automação de browser (Playwright/Chrome já logado) com a **sua própria sessão**, sem nunca falar com a API privada, sem senha em terceiro, e com o story sendo criado por um produto da própria Meta — indistinguível de um agendamento manual. O risco cai de "impersonar o app móvel" para "automatizar meu próprio painel", que é uma categoria de risco muito diferente. Continua zona cinzenta de termos, e a UI quebra a cada redesign, mas não é a mesma coisa.

---

## 5. Automação de dispositivo (Appium / ADB / Tasker)

### 5.1 Viabilidade técnica

Alta. O fluxo manual tem ~8 toques (abrir stories → carregar mídia → ícone de sticker → "Link" → colar URL → editar texto do CTA → posicionar → publicar) — **MÉDIA**. 6 execuções/dia é volume irrisório para Appium/UIAutomator2 sobre ADB. Ferramentas de referência: Appium + UIAutomator2, e o projeto `insomniac` (bot de Instagram via ADB, sem root, usando UI Automator) como prova de que o padrão funciona. — **MÉDIA**

**O ponto forte único deste caminho:** o artefato publicado é **100% nativo**. Foi o app oficial do Instagram, no aparelho, que criou o sticker. Não há nada no story publicado que possa ser detectado a posteriori. O risco migra inteiramente do *conteúdo* para o *ambiente*.

### 5.2 Risco de detecção — evidência fraca, e eu vou dizer isso

**Não encontrei nenhuma fonte confiável de 2025–2026 quantificando detecção de Appium/ADB no Instagram.** O que existe é fórum (BlackHatWorld, com dois tópicos ativos sobre "iPhone farm" e "iOS Instagram Automation with Appium — Architecture Review & Scaling Questions", ambos bloqueados para fetch — 403) e discussões do Appium. Tudo **ANEDÓTICA**.

O que dá para dizer com base em como esses sistemas funcionam (inferência, **ANEDÓTICA/MÉDIA**):

- **Emulador é bem mais detectável que aparelho físico** — build props, ausência de sensores, GPU de software, ausência de atestação de integridade de hardware.
- **Celular Android físico + rede residencial/4G + ADB por USB** é a variante de menor pegada. O Instagram vê um aparelho real, com histórico real, num IP residencial brasileiro consistente.
- O que costuma disparar não é a automação em si e sim **velocidade sobre-humana e ausência total de variação**. 6 posts/dia com jitter aleatório e horários irregulares fica dentro do comportamento humano plausível.

### 5.3 Custo operacional real (que é o que mata este caminho)

- Um celular Android dedicado, ligado, com bateria gerenciada, e **um host sempre ligado** rodando o driver Appium.
- Transferir a imagem gerada pelo pipeline para o aparelho a cada ciclo.
- **A UI do Instagram muda a cada atualização do app.** Cada mudança na bandeja de figurinhas quebra o script. E, ao contrário do instagrapi, você não tem um mantenedor consertando para você.
- Verificação obrigatória por screenshot + OCR, ou você não sabe se o sticker entrou.
- Recuperação de erro: pop-up de "novo recurso", atualização forçada, prompt de login, notificação sobrepondo o botão.

**Veredito:** viável para 6/dia unattended, mas é o caminho de **maior custo de manutenção contínua** da lista. Faz sentido se você tem um aparelho sobrando e trata isso como infraestrutura permanente. Não faz sentido como atalho.

---

## 6. Contornos que evitam o sticker — e o que custam em cliques

### 6.1 "Arraste para cima" — morto, confirmado

- Removido em **30 de agosto de 2021**, substituído pelo sticker de link. — **MÉDIA/FORTE** ([Social Media Today](https://www.socialmediatoday.com/news/instagram-is-removing-swipe-up-links-for-stories-replacing-the-function-wi/605437/); [TechCrunch, 2021-08-23](https://techcrunch.com/2021/08/23/instagram-is-ditching-swipe-up-links-in-favor-of-stickers/))

### 6.2 O sticker de link liberado para todos — o dono está certo, e a data é 2021

- O sticker de link estreou em **agosto de 2021** limitado a contas verificadas ou com **10.000+ seguidores**.
- **Adam Mosseri anunciou a remoção do requisito de 10k**, com liberação para **todos** a partir de **26–30 de outubro de 2021**. — **MÉDIA** (imprensa consistente: WERSM, ACTIVATE, TechRadar, GSMArena)
- Em 2026 segue assim: qualquer conta profissional, sem mínimo de seguidores, sem selo. — **MÉDIA**

⚠️ **Correção importante:** várias fontes de 2026 dizem "2022". **Está errado** — foi outubro de 2021. O dono está correto.

⚠️ Ressalva operacional relatada (**ANEDÓTICA**): contas muito novas às vezes esperam dias até o sticker aparecer (antispam), e contas com strike de diretrizes podem ter o link temporariamente restrito.

### 6.3 Link na bio (Linktree e afins)

- **Custo em cliques:** benchmarks 2025–2026 dão para o sticker de link **~1,2% (orgânico)** a **~4,1% de mediana** de tap-through, com bons criadores em 6–7%; e para **bio ~1–2%**. — **MÉDIA** (blogs de benchmark: IQfluence 2025, Dash Social, Socialinsider — não são fontes primárias)
- Em números redondos: **perde-se algo entre 2x e 4x em cliques**. Você não perde 100%.
- **Vantagem que quase ninguém pondera:** risco **zero**, custo **zero**, e funciona com o caminho `media_type=STORIES` que **você já validou ao vivo**. A URL da bio nunca muda; quem muda é a página de destino (que seu pipeline já controla).
- Não existe endpoint na Graph API para alterar a bio/link do perfil — então a página de destino tem que ser estável e o conteúdo dela é que rotaciona. **FORTE** por ausência na referência.

### 6.4 Sticker de menção via `user_tags`

- Oficialmente suportado em stories desde **2025-07-09**. — **FORTE**
- **Mas** a mesma página diz: *"publishing stickers … is not supported; **however mentioning users without a sticker is supported**"*. Ou seja: a menção é registrada, **sem desenhar o sticker**. — **FORTE**
- **Marcado como NÃO VERIFICADO:** eu não achei nenhuma demonstração visual de como isso renderiza no app em 2026. Não sei dizer se fica tocável.
- **Mesmo no melhor caso, não resolve:** uma menção aponta para um *perfil*, não para uma URL. O usuário teria que tocar a menção → abrir o perfil → tocar o link da bio. São **mais toques que o link na bio direto**. Não vejo cenário em que isto ganhe.

### 6.5 Sticker de produto / Instagram Shopping

Três bloqueios independentes, e o terceiro é fatal:

1. `product_tags` — *"Applies only to images and videos"* (feed), não stories. — **FORTE**
2. O guia de content publishing: *"Shopping tags are not supported."* — **FORTE**
3. **Você não tem catálogo.** Product tags exigem um catálogo do Meta Commerce com produtos que **você vende**, sujeito à Commerce Policy e ao merchant agreement, vendendo produtos físicos próprios. Um afiliado divulgando SKU de terceiro no Shopee/ML **não é elegível**. — **MÉDIA**

Contexto Brasil: o Instagram Shopping segue disponível no país e product tags/catalog ads ainda funcionam, mas a Meta removeu a aba Shop e o checkout nativo — a compra acontece no site do lojista. — **MÉDIA**

**Não é caminho.**

### 6.6 "Link no canal do Telegram"

- Risco zero, custo zero, e você **já tem o canal** alimentado pelo mesmo pipeline.
- Custo em cliques: adiciona uma troca de app. Não achei benchmark. — **ANEDÓTICA**
- **Vantagem estratégica real:** move o seguidor de uma superfície alugada (Instagram) para uma que você controla, onde o link é sempre clicável, sem cota, sem sticker, sem risco. Para afiliado, migrar audiência para o Telegram costuma valer mais que 2x de CTR num story efêmero de 24h. — inferência, **ANEDÓTICA**, mas é a jogada de negócio que eu defenderia.

---

## 7. Shopee Video / Mercado Livre — superfícies alternativas?

### 7.1 Shopee

- A **Shopee Affiliate Open API** (`affiliate.shopee.com.br/open_api`) usa **GraphQL + HMAC-SHA256**, com App ID/API Key gerados no painel de afiliado. Escopo: **geração de link e relatórios**. **Não encontrei nenhum endpoint de publicação de vídeo/conteúdo.** — **MÉDIA** (a página em si não abre para fetch — é SPA)
- **Shopee Video** (o feed de vídeo curto onde afiliados anexam produtos ao carrinho) é **fluxo manual no app**: favoritar produtos → subir vídeo → adicionar produtos ao carrinho da vitrine. — **MÉDIA**
- O **Shopee Open Platform** (lado *seller*) tem MediaSpace com `upload_image` / `upload_video`, mas isso serve para mídia de **anúncio de produto do vendedor**, não para postar no feed Shopee Video como afiliado. — **MÉDIA**
- ⚠️ **Alerta relevante para o seu pipeline:** a Shopee publicou em 2026 uma política de **AI-UGC** — se você **rotular** o conteúdo como gerado por IA, **as comissões Shopee são desligadas** (você continua elegível a comissões AMS). — **MÉDIA**. Se o pipeline gera criativos por IA, leia essa política antes de escalar Shopee Video.

### 7.2 Mercado Livre

- **Mercado Clips** é vídeo curto **atrelado ao anúncio do próprio vendedor**, aparecendo na home do app/site, no Mercado Play e na seção Clips. Em 2026 ganhou criação assistida por IA (voz, legenda, trilha) gratuita. — **MÉDIA**
- É **ferramenta de seller, não de afiliado**: o Clip existe preso a um `item_id` que é seu.
- **Não encontrei API pública de publicação de Clips** em `developers.mercadolivre.com.br` — o portal cobre publicação de itens, catálogo, vendas, envios. — **MÉDIA** (ausência; não é prova definitiva)

### 7.3 Veredito

**Nenhuma das duas é superfície alternativa utilizável por um afiliado via API.** Shopee Video é manual; Mercado Clips é de vendedor. Nenhuma resolve o problema do sticker de link no Instagram.

---

## Tabela de decisão final

| # | Caminho | Sticker fica clicável? | Esforço | Custo | Risco de perder a conta | Recomendação |
|---|---|---|---|---|---|---|
| **1** | **Graph API `media_type=STORIES`** (o que você já tem) **+ CTA "link na bio" / "link no Telegram" na própria arte** | **Não** | **Nenhum** (já funciona) | **R$0** | **Baixo** — caminho oficial, permissão concedida, 6/100 da cota diária. Meta nega stickers mas abençoa o post. **FORTE** | ✅ **Manter como base para os 6/dia.** Perde 2–4x em cliques vs sticker (**MÉDIA**), ganha 100% de estabilidade. |
| **2** | **Meta Business Suite** agendando story com figurinha de link (manual ou dirigido por browser) | **Sim, se a figurinha existir lá** | Baixo manual / médio-alto se automatizar (Playwright, UI quebra) | **R$0** | **Baixo–médio** — produto de 1ª parte da Meta, sua própria sessão logada, nada impersona o app móvel. Automatizar entra na zona cinzenta de "automated means" do ToS Meta (vigente 2025-01-01). **MÉDIA, e a premissa está CONTESTADA** | ⭐ **VERIFICAR PRIMEIRO — 5 minutos.** Se a figurinha "Link" existir no compositor de story do MBS, este é o melhor caminho do relatório e reordena tudo abaixo. |
| **3** | **Anúncio Stories** via Marketing API (`instagram_positions: ['story']`) | **Sim** (botão/CTA), mas é **anúncio**, não story orgânico — não aparece no seu anel para seguidores | Médio (conta de anúncio, criativo, revisão) | **~R$800–1.000/mês** no piso técnico; **R$1.600–6.500/mês** para sair do learning. **MÉDIA** | **Muito baixo** — é o produto pago da Meta. Shopee permite impulsionar da rede social cadastrada (**FORTE**); regras do ML **não verificadas** | ⚠️ Só se quiser **comprar alcance**. Não substitui os 6 stories/dia. Confirme os T&C de afiliado do Mercado Livre antes. |
| **4** | **Automação de dispositivo** — Android físico + Appium/ADB | **Sim, e 100% nativo** (o app oficial criou o sticker) | **Alto e contínuo** — aparelho dedicado, host 24/7, script quebra a cada update do IG, verificação por screenshot | Hardware + energia; ~R$0 de software | **Médio** — nada detectável no story publicado; risco é fingerprint de aparelho/emulador. **Emulador = pior; celular físico + IP residencial = melhor.** Evidência **ANEDÓTICA** — não há dado público de 2025–2026 | 🟡 Plano B se o MBS não servir e você aceitar manter infraestrutura. Nunca em emulador. |
| **5** | **Storrito** (US$19/mês/conta ≈ **R$103/mês**), com API HTTP própria | **Sim** — único agendador comercial que faz, e automático | **Baixo** — tem API JSON pronta (`schedule-instagram-story`) | **~R$103/mês por conta** | **Alto** — pede **usuário e senha** do Instagram; "no business account required" ⇒ é API privada. **Zero divulgação de risco no site.** **FORTE** | ❌ **Não usar em `@ofiscaldapromo`.** Mesmo risco do instagrapi + sua senha num terceiro. Se testar, use conta descartável. |
| **6** | **instagrapi** self-hosted (`StoryLink(webUri=…)`) | **Sim** — quando funciona | Médio; **verificação obrigatória** com `story_info()` após cada post | R$0 + proxy residencial | **Alto** — o **próprio mantenedor** diz que é "best suited for testing, research, and controlled internal automation" e manda usar HikerAPI em produção (**FORTE**). Exige senha; 2FA só TOTP, **não SMS**. Ficou **quebrado em silêncio de 2025-11-03 a 2026-04-16** (**FORTE**). Bans no tracker são majoritariamente de follow/scrape, mas `challenge_required` é recorrente em 2026 e o projeto criou `AccountSuspended` em jul/2026 | ❌ **Não na conta principal.** Sem taxa de ban publicada e confiável — lacuna real. Se insistir: conta secundária, IP residencial fixo, device profile estável, verificação pós-publicação. |
| **7** | **Sticker de menção** via `user_tags` | **Provavelmente não** — Meta diz "mentioning users **without a sticker**". **NÃO VERIFICADO** como renderiza | Baixo | R$0 | **Baixo** | ❌ Mesmo se funcionasse, aponta para perfil, não para URL — **mais toques que link na bio**. Sem sentido. |
| **8** | **Sticker de produto / Shopping** | **Não** | — | — | — | ❌ Bloqueado três vezes: API não suporta em stories, guia diz "Shopping tags are not supported", e **afiliado não tem catálogo Meta Commerce elegível**. |
| **9** | **Shopee Video / Mercado Clips** como superfície alternativa | n/a | — | — | — | ❌ Shopee Video é manual no app; Mercado Clips é de vendedor (preso ao próprio anúncio). Nenhuma API pública de publicação. |
| **10** | Agendadores (Later, Buffer, Metricool, Publer, Sprout, mLabs, Etus) | **Não** — todos caem em notificação, exige toque humano | — | — | Baixo | ❌ Não atende "sem tocar na tela". Serve só como **prova convergente** de que não existe endpoint oficial escondido. |

---

## VEREDITO FINAL (2026-08-27, depois da verificação do dono)

O caminho #2 caiu: o Meta Business Suite mostra link em story **só no
Facebook**. Com ele fora, **não existe caminho de primeira parte** que ponha
figurinha de link em story do Instagram. Sobram três famílias, todas com o
mesmo preço:

| O que resta | Preço |
|---|---|
| API privada (instagrapi, Storrito) | a **senha** da conta + histórico de quebra silenciosa |
| Automação de aparelho Android | infraestrutura 24/7 que quebra a cada update do app |
| Anúncio de Stories | R$ 800+/mês e **não** aparece no anel do perfil |

**Decisão: ficamos no caminho #1** (Graph API, sem figurinha). O que muda é
que a compensação deixa de ser opcional — ver abaixo.

### A compensação: fazer o "link na bio" valer a pena

A perda medida do sticker para o link na bio é de 2 a 4 vezes em cliques, mas
esse número pressupõe uma bio que leva a lugar nenhum. Duas coisas recuperam
boa parte dela, ambas de custo zero e risco zero:

1. **Uma página de ofertas do dia gerada pelo próprio pipeline**, publicada no
   GitHub Pages que o projeto já usa (`jmbessa.github.io/fiscal-da-promo`, hoje
   só serve de callback do OAuth do ML). O story manda para a bio, a bio leva a
   uma lista das ofertas de hoje com os links de afiliado — em vez de um perfil
   estático. É o mesmo dado que já temos no `state.db`; sai como fase 5D.
2. **A arte do story trabalhando mais o CTA**: "LINK NA BIO" legível de longe,
   seta apontando para o topo, e a menção ao canal do Telegram — onde o link
   **é** clicável e a conversão não tem intermediário.

Vale lembrar do que a pesquisa de crescimento já tinha estabelecido: **story
não traz seguidor novo**, ele converte quem já segue. O canal do Telegram é a
superfície onde o clique acontece sem atrito. Perder o sticker dói menos neste
projeto do que doeria num que vivesse só de Instagram.

## Recomendação operacional

**Ordem de ação:**

1. **Hoje, 5 minutos, antes de escrever qualquer código:** abrir `business.facebook.com` como `@ofiscaldapromo`, criar um story, e verificar se a figurinha **"Link"** existe no compositor. Este único teste decide entre o caminho #2 (ótimo) e o resto (todos com trade-off ruim). A evidência a favor tem 3 fontes de 2026, a contra tem 2 — não dá para resolver por pesquisa, só por observação.
2. **Enquanto isso, manter os 6/dia no caminho #1.** Ele já está validado ao vivo, custa nada, e a perda de 2–4x em cliques é recuperável melhorando a arte (CTA grande, "LINK NA BIO" legível, seta apontando).
3. **Não misturar caminhos na mesma conta.** Se um dia testar instagrapi ou Storrito, faça numa conta descartável primeiro, e nunca rode instagrapi e Graph API na mesma conta no mesmo dia — sessões de app móvel forjadas convivendo com publicações oficiais é justamente o padrão que chama atenção.
4. **Se o MBS servir, não automatizar os 6.** Automatize 1–2 stories/dia (as melhores ofertas) pelo MBS e deixe os outros 4–5 na Graph API. Limita o raio de dano se a automação de browser for detectada ou se a UI quebrar.

## Lacunas de evidência que eu não consegui fechar (declaradas de propósito)

1. ~~**Meta Business Suite suporta figurinha de link em story agendado?**~~
   **FECHADA em 2026-08-27 pelo dono, no compositor real.** O MBS oferece link
   em story, mas com o aviso: *"Os links serão mostrados apenas nos stories do
   Facebook."* Ou seja: **não vale para o Instagram.** O caminho #2 da tabela
   está MORTO — as três fontes de 2026 que diziam o contrário estavam
   generalizando o comportamento do Facebook para o Instagram. Evidência:
   **FORTE** (observação direta no produto, com a conta do projeto).
2. **Taxa real de ban do instagrapi para contas Business que apenas publicam.** Não existe dado público confiável. Os números "0,5% vs 15–30%" que circulam são marketing sem fonte.
3. **Detecção de Appium/ADB pelo Instagram em 2025–2026.** Só fórum, e os tópicos relevantes bloqueiam acesso automatizado (403).
4. **Como `user_tags` em story renderiza visualmente** no app em 2026.
5. **Regras de tráfego pago do programa de afiliados do Mercado Livre.** Fontes brasileiras secundárias se contradizem; os T&C oficiais precisam ser lidos direto.
6. **Câmbio BRL/USD de agosto de 2026** — usei ~R$5,40 como aproximação. Confirme antes de usar os números de custo.
