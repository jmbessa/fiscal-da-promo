# A anatomia do Reel que prende, e o carrossel temático vale a pena?

> Pesquisa de 2026-08-31, a pedido do dono. Duas perguntas:
> 1. *"como devem ser os formatos dos reels para chamarem a atenção dos usuários?"*
> 2. *"o que acha da publicação de carrossel no feed? poderíamos escolher temas
>    relevantes e fazer os posts. vale a pena?"*

## Como ler a força da evidência

- **FORTE** — estudo com amostra e período declarados, ou documentação/anúncio
  oficial da Meta.
- **MÉDIA** — fonte séria mas com método parcial, ou síntese minha conciliando
  duas fontes fortes.
- **ANEDÓTICA** — blog de agência ou de ferramenta, sem amostra nem método. A
  maior parte do que se escreve sobre "gancho de Reels" é isto. Não some, mas
  não decide.

## O que já está respondido, e não se repete aqui

Estas perguntas foram fechadas em pesquisas anteriores e **não** são refeitas:

- **Qual formato escolher** (Reel vs carrossel vs imagem única), duração,
  hashtags e cadência — `2026-08-28-pesquisa-feed.md`.
- **O que a conta pode postar sem se desmentir** (a invariante da bio, o
  enquadramento "acuse a prática, aprove o produto") —
  `2026-08-29-feed-sem-contradicao.md`.
- **Como contas de promoção crescem do zero** — `2026-08-26-pesquisa-crescimento.md`.

O que falta, e é o objeto deste documento: a **anatomia** do Reel (o que
acontece em cada segundo dele) e se carrossel **temático** — sem produto — vale
a cota de publicação.

---

# Parte I — O Reel

## 1. Os três primeiros segundos são o jogo inteiro, e agora a Meta mede isso

O fato que organiza tudo o resto: em **abril de 2026** o Instagram trocou a
métrica `view rate` por **`skip rate`** nas Insights — quantos saem nos
**3 primeiros segundos**. E Mosseri, em janeiro de 2026, confirmou **watch time
e completion rate como o sinal de ranqueamento mais importante** dos Reels.
**FORTE** (mudança de produto e declaração oficial, rastreáveis).

A leitura prática: a Meta passou a publicar, para cada peça nossa, a nota do
gancho. Isso é uma sorte — a conta tem 2 seguidores e nenhuma leitura própria
de audiência; o `skip_rate` é o primeiro número honesto que ela vai receber.
Ele já está previsto no P4 do brief (`media_reel_skip_rate` via Windsor).

Os números que circulam sobre o tamanho da janela — "decisão em 1,7 s", "até
50% saem em 3 s", "gancho acima de 60% de retenção rende 5–10× mais alcance" —
vêm todos de blogs de ferramenta, sem amostra nem método. **ANEDÓTICA.**
Convergem numa direção plausível e coerente com a mudança oficial, mas nenhum
deles é base para calibrar nada. O que dá para afirmar com segurança é o que a
própria Meta fez: ela mede os 3 segundos e ranqueia por retenção.

Um número **FORTE** ancora a escala: o Metricool mediu, em **24.364.803 posts
de 375.118 contas** (jan–fev/2025 a jan–fev/2026), **watch time médio de 8,5
segundos** — mais que o dobro do ano anterior. Ou seja: a média das pessoas
assiste 8,5 s de Reel. O que a peça tem a dizer precisa caber aí.

## 2. A contradição de duração continua, e para o nosso caso ela se resolve

Duas fontes fortes apontam para lados opostos, e isso não mudou desde a
pesquisa de 28/08:

- **Socialinsider, 6 milhões de Reels (jan–jun/2026):** a faixa **45–60 s** teve
  a maior taxa de engajamento (0,35%) e a maior mediana de views (~10.374) —
  cerca do dobro de clipes com menos de 30 s. **MÉDIA** (não consegui abrir o
  estudo original; li a citação).
- **Metricool (FORTE, acima):** watch time médio de 8,5 s.

Os dois podem estar certos ao mesmo tempo, e a ponte é o `completion rate` que
Mosseri nomeou: um Reel de 50 s assistido por 8,5 s tem **17% de completion**;
um de 8 s assistido inteiro tem **100%** e ainda entra em loop. A faixa de
45–60 s ganha quando **existe história para contar**; ela perde feio quando não
existe, porque a métrica que manda castiga o abandono.

**Para o nosso caso, a peça curta é a escolha certa — por ora.** O Reel atual
tem 8 s e mostra um produto, um preço e um selo. Isso não é uma história de
50 segundos; esticá-lo seria comprar o pior lado das duas evidências.
**MÉDIA** (é minha síntese, não um achado publicado).

Isso deixa uma porta aberta que vale registrar: se um dia existir a peça
"**a série de preço deste produto nos últimos 90 dias**", com curva, com o
momento em que o vendedor inflou o "de" e com o veredito no fim, **aí** há
história para 45–60 s, e aí a faixa longa passa a valer o teste. Hoje não há —
`price_refs` é 0.

## 3. O áudio: a API nos tranca fora da faixa de descoberta

Este é o achado mais duro da pesquisa, e ele é estrutural.

**A Content Publishing API não anexa áudio da biblioteca do Instagram.** Nem
som em alta, nem catálogo, nem efeito. Se o Reel precisa de som, ele tem de
estar **embutido no arquivo antes do upload**. **FORTE** (é limitação
documentada da API e repetida por todas as ferramentas de agendamento; nenhuma
delas publica Reel com áudio licenciado sem passo manual no app).

Consequências, em ordem de importância:

1. **A faixa de descoberta por som está fechada para um pipeline automatizado.**
   O "áudio em alta" é um canal de distribuição próprio (a página do som), e
   nós não temos acesso a ele por API. Quem quiser usá-lo tem de publicar **à
   mão, pelo celular** — o que contradiz o projeto inteiro.
2. **Silêncio não é penalidade, mas é desperdício.** Não achei nenhuma fonte
   confiável de que Reel mudo seja rebaixado no orgânico; a alegação de "72%
   menos distribuição" que circula é atribuída a um vazamento não verificável
   — **ANEDÓTICA, não usar.** O que se perde não é ranking, é o canal extra.
3. **O caminho que sobra é áudio ORIGINAL embutido — e ele é totalmente
   automatizável.** Uma narração TTS gravada no `.mp4` é áudio original, é
   nosso, não tem problema de licença, e as fontes convergem em que áudio
   original não é punido (**MÉDIA**). O projeto já tinha proposto isso
   (`2026-08-28`, "TTS + mascote") e a limitação da API transforma a proposta em
   **a única opção viável**.

**O Reel de hoje sai com uma faixa AAC silenciosa** (`anullsrc` em
`video.py:117`). Ela existe só porque a Meta rejeita container sem faixa de
áudio. Substituí-la por narração é a mudança de maior alavanca desta pesquisa.

## 4. Uma coisa que a pesquisa deixa clara e que a nossa peça erra

Cruzando o que se sabe com o que o `creative.reel_frames` faz hoje:

| segundo | o que o espectador vê hoje |
|---|---|
| 0,00 | fundo, cabeçalho da marca, foto do produto, badge de % (quando há) |
| 0,10–0,80 | o título **entra** |
| 0,70–1,40 | a pílula de preço **entra** |
| 1,30–1,80 | a prova social entra |
| 1,70–2,30 | o selo entra |
| 2,30–8,00 | a peça parada, com o zoom correndo |

O preço — o único motivo pelo qual alguém pararia — **só termina de aparecer em
1,4 s**, e no frame zero a peça está literalmente sem texto. Estamos gastando a
fração mais cara do vídeo animando a chegada dos elementos em vez de mostrar o
que eles dizem.

Some-se a isso: **não existe gancho verbal em lugar nenhum da peça.** A arte
diz *o quê* (título), *quanto* (preço) e *por que acreditar* (selo). Nenhuma
linha diz *por que parar*. As fontes de gancho são todas ANEDÓTICAS quanto a
números, mas todas concordam num ponto que também é o que a Meta descreve ao
falar de `skip rate`: a primeira coisa legível, no mudo, em ~6 a 8 palavras, é o
que decide.

**A correção não é reescrever a peça — é inverter a ordem.** Frame zero com o
preço e uma linha de gancho já postos; a animação vira **ênfase** (o zoom, o
selo chegando) em vez de **carregamento**.

## 5. Capa ≠ primeiro frame

Dois trabalhos diferentes, e vale saber que são dois: o **primeiro frame** é o
que toca sozinho quando alguém passa no feed de Reels; a **capa** é o quadro
congelado na grade do perfil e no Explorar. **MÉDIA.** Sem capa escolhida, o
Instagram usa o primeiro frame — que hoje, no nosso caso, é a peça sem título e
sem preço. Corrigir o item 4 corrige os dois de uma vez.

## 6. Trial Reels: está na API, e é feito sob medida para 2 seguidores

Um Trial Reel é servido **só para quem NÃO segue a conta**, por 72 h, e não
aparece para os seguidores nem na grade. Depois disso o Instagram decide.

**A API aceita.** O parâmetro é `trial_params`, com
`graduation_strategy` valendo `MANUAL` ou `SS_PERFORMANCE` (o Instagram promove
sozinho se a peça bater o limiar). **FORTE** — está na documentação de
publicação da Meta, junto de `media_type`, `video_url`, `caption` e
`is_ai_generated`.

Para uma conta com **2 seguidores**, isso é exatamente a bancada de teste que o
brief pediu: 100% da entrega vai para desconhecidos, que é o público que
importa, e cada peça volta com retenção medida contra um público frio.

**A ressalva, e ela é séria:** várias fontes afirmam que Trial Reels exigem
**1.000 seguidores**. **ANEDÓTICA** — todas são blogs, e a documentação da API
que li não traz esse requisito. É barato descobrir: mandar um container com
`trial_params` e ler o erro. Não dá para presumir nenhum dos dois lados.

---

# Parte II — O carrossel

## 7. O número que muda a resposta

O brief de melhorias colocou Reels como P0 com o argumento "Reel alcança
não-seguidor, carrossel serve quem já segue". Esse argumento vem de uma média
global. **Quebrando por tamanho de conta, ele se inverte na nossa faixa.**

Socialinsider, **35 milhões de posts de 447.613 páginas** (jan–dez/2025),
**views médias por post**:

| seguidores | Reels | Carrossel | Imagem |
|---|---:|---:|---:|
| **1–5 mil** | 580 | **993** | 417 |
| 5–10 mil | 1.000 | **2.117** | 1.068 |
| 10–50 mil | 2.460 | **4.275** | 2.340 |
| 100 mil–1 M | 16.035 | **35.370** | 22.900 |

**FORTE** quanto à amostra. Na menor faixa medida, o carrossel entrega
**1,7× as views do Reel**.

**A ressalva que impede de ler isso como "carrossel ganha", e ela é decisiva:**
o carrossel é o **único formato que o Instagram pode remostrar** no feed da
mesma pessoa (com a segunda imagem, para quem não engajou na primeira). Ou
seja, parte dessa vantagem em *views* é **contagem**, não gente nova. O Buffer,
medindo **alcance** (pessoas distintas), acha o contrário: Reels com **1,36× o
alcance** do carrossel. **FORTE** também.

**Conciliação — e é minha, MÉDIA:** para adquirir **pessoas novas**, Reel
continua sendo o formato. Para ser **visto mais vezes por quem já cruzou com a
conta**, e para ser **salvo**, o carrossel ganha — e ganha mais quanto menor a
conta. Não são substitutos: são funções diferentes.

## 8. Saves são a moeda do carrossel, e conteúdo temático é o que se salva

Metricool (mesma amostra de 24,3 M): **carrossel gera 9× mais saves que imagem
única** e supera imagem única em toda métrica. **FORTE.**
Socialinsider, saves médios por post na faixa de 50–100 mil seguidores:
carrossel 35, Reels 22. **FORTE.**

E há um dado brasileiro forte já registrado na pesquisa de 28/08: **36% dos
brasileiros salvam post para comprar depois.** Save, para este nicho, não é
vaidade — é intenção de compra adiada.

O que se salva é **utilidade transferível** — a peça que serve de novo semana
que vem. Um post de produto expira quando a oferta expira; um post de método
não expira. Todas as fontes que li sobre carrossel educativo são de agência
(**ANEDÓTICA** nos números), mas o mecanismo é o mesmo que o dado forte acima
descreve, e não depende delas.

## 9. E tem um motivo que não é de marketing: a regra de originalidade

Desde **30/04/2026** o Instagram deixou de recomendar a não-seguidores contas
que publicam principalmente conteúdo que não criaram nem editaram de forma
material (**FORTE**, já documentado em `2026-08-28-pesquisa-feed.md`).

Um post de produto é foto do vendedor com nossa moldura em volta. A moldura é
gráfico próprio e provavelmente basta — mas é a parte **fraca** do argumento, e
ela é 100% do que a conta publica hoje. **Um carrossel temático é conteúdo
integralmente nosso**: texto nosso, gráfico nosso, tese nossa. Ele não é só
mais um formato; ele é a prova de originalidade que o resto do feed não tem.

Isso responde "vale a pena?" por um caminho que não é o do alcance: **vale
porque protege a elegibilidade de recomendação de todo o resto.**

## 10. Os temas que a conta pode defender — e o filtro que eles têm de passar

A pesquisa de 29/08 já fixou a invariante: **acuse a PRÁTICA, aprove o
PRODUTO**; nunca nomeie um produto reprovado. Todo tema abaixo passa por ela.

Temas que a marca "Fiscal da Promo" sustenta sem esticar nada:

1. **"Como eu confiro um desconto"** — o método, com a régua real (p25, janela,
   por que 14 dias). É o conteúdo que a bio promete e o feed nunca entregou.
2. **"O 'de' que não existe"** — a prática da âncora inflada, explicada em
   genérico, com o que o Procon recomenda. Sem nomear ninguém.
3. **"O que o Fiscal reprovou esta semana"** — agregado e anônimo: "14 não
   passaram; 9 tinham 'de' inventado". Número nosso, zero exposição.
4. **"Vale a pena esperar?"** — só quando houver padrão medido na nossa série.
   **Hoje não há** (`price_refs` = 0).
5. **"As N do dia com selo do Fiscal"** — a vitrine das que passaram. Também
   **bloqueada hoje** pelo mesmo motivo: sem régua não há modo A, e a peça
   sairia vazia todo dia.

Os três primeiros **funcionam hoje, sem nenhum dado novo**. É a diferença que
importa: eles não dependem do `price_refs` que está travando o resto da fila.

## 11. O custo, que é onde a ideia costuma morrer

O pipeline gera carrossel bem (Pillow, 1080×1350 — que é o 4:5 recomendado, e
já é o `FEED_SIZE` do projeto). O que ele **não** gera é a tese.

- Um carrossel de produto é template com slots: **~0 min de humano**.
- Um carrossel temático é redação: **20–30 min na primeira vez**.

A saída é que **conteúdo de método é perene**. Escrito uma vez, ele volta ao ar
a cada 4–6 semanas com números atualizados pelo pipeline. Não é conteúdo diário
escrito à mão — é um **acervo de 6 a 8 peças** que roda. Com 1 h/dia de humano
disponível, isso é uma semana de trabalho para um ano de conteúdo.

Número de slides: a evidência continua **ANEDÓTICA e conflitante** (6–8 numa
fonte BR, 8–10 em blogs internacionais). Fico em **6–8**, porque é mais barato
de gerar e revisar, não porque haja prova.

---

# 12. O que eu recomendo

**Sobre o Reel — três mudanças, em ordem de alavanca:**

1. **Inverter a animação.** Frame zero já com preço, título e uma linha de
   gancho legível no mudo. A animação vira ênfase, não carregamento. É a única
   mudança que ataca diretamente o `skip_rate`, que é a métrica que a Meta
   passou a publicar.
2. **Trocar o silêncio por narração TTS.** É a única forma de ter áudio numa
   peça publicada por API, é original, e é 100% automatizável.
3. **Publicar como Trial Reel** (`trial_params`), enquanto a conta tiver
   audiência desprezível: 100% da entrega vai para não-seguidores. Testar o
   requisito de 1.000 seguidores em vez de presumi-lo.

Manter os **8 segundos** por enquanto. Reavaliar a faixa de 45–60 s no dia em
que existir a peça de série histórica.

**Sobre o carrossel: sim, vale — e por um motivo diferente do que se supõe.**
Não é "carrossel alcança mais" (isso é parcialmente artefato de contagem). É:

- é o formato mais **salvo**, e save é intenção de compra num público onde 36%
  salvam para comprar depois;
- na nossa faixa de tamanho ele entrega **mais views que Reel**, ainda que
  parte disso seja remostra;
- e é o **único conteúdo integralmente original** que a conta teria — o que
  protege a elegibilidade de recomendação do feed inteiro sob a regra de
  30/04/2026.

Com **temas escolhidos**, exatamente como você propôs, e não como post de
produto. Começar pelos três que **não dependem da régua**: o método, a prática
do "de" inflado, e o agregado semanal anônimo.

Divisão que eu proporia: **Reel como aquisição, carrossel temático como
retenção e prova de originalidade, post de produto como conversão.** Três
funções, três formatos, nenhum deles substituindo o outro.

# 13. O que eu NÃO consegui estabelecer

- **Nenhum número confiável sobre ganchos.** Tudo o que existe sobre "hook
  formulas" e retenção em 3 s é blog sem amostra. O que é sólido é a mudança de
  produto da Meta, não os percentuais.
- **Se Trial Reels exigem 1.000 seguidores.** Fontes secundárias dizem que sim;
  a documentação da API que li não diz. Precisa de teste ao vivo.
- **Se conteúdo de método converte seguidor no Brasil neste nicho.** Continua
  sem precedente medido — o Pelando, dono da maior base de histórico de preço
  do país, tem 33 mil seguidores. É aposta com fundamento, não estratégia
  validada. Isto já estava registrado em 28/08 e não mudou.
- **Nada disto foi testado na nossa conta**, porque a conta nunca publicou um
  Reel. O primeiro Reel ao vivo vale mais que qualquer parágrafo acima.

## Fontes

- [Metricool — estudo de Instagram 2026 (24.364.803 posts, 375.118 contas)](https://metricool.com/press-release-instagram-study-2026/)
- [Socialinsider — benchmarks de Instagram 2026 (35 M de posts, 447.613 páginas)](https://www.socialinsider.io/social-media-benchmarks/instagram)
- [Meta — Publish Content using the Instagram Platform (`trial_params`, `media_type`, limite de 100 posts/24 h)](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [Metricool — Instagram Reel Analytics: retention e skip rate](https://metricool.com/instagram-reel-analytics/)
- [Socialinsider via moonb.io — duração de Reels, 6 M de Reels jan–jun/2026](https://www.moonb.io/blog/instagram-reel-length)
- [Statusbrew — áudio em alta não é publicável por ferramenta de terceiro](https://statusbrew.com/insights/adding-trending-audio-to-scheduled-content)
- [Postproxy — guia de publicação de Reels por API (2026)](https://postproxy.dev/blog/instagram-reels-api-publishing-guide/)
- [PostFast — Trial Reels (requisito de 1.000 seguidores; ANEDÓTICA)](https://postfa.st/blog/instagram-trial-reels)
