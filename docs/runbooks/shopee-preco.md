# O preço da Shopee: o que a API entrega, e o que só o navegador entrega

Investigação de **2026-08-28**, provocada por um caso real: o story publicou
**R$ 689,99** e o dono abriu o anúncio e viu **R$ 611,80**.

## O veredito: o story estava CERTO

A página da Shopee diz, em duas linhas:

> **R$ 611,80** no Pix com cupom
> ou **R$ 689,99 sem cupom** em outros métodos de pagamento

R$ 689,99 é o preço que qualquer pessoa paga sem cupom. R$ 611,80 exige **Pix
mais cupom** — é desconto de **checkout**, não preço de catálogo.

O problema não é de exatidão, é de leitura: a Shopee põe os R$ 611,80 em
vermelho grande e os R$ 689,99 em cinza pequeno. Quem clica bate o olho no
número menor e conclui que o nosso está velho.

## A OITAVA rota RESOLVE — e ela não passa por navegador nenhum (fase 5R, 2026-08-29)

**Leia esta seção primeiro.** As sete abaixo continuam valendo e são a razão de
esta existir; elas são a história de como se chegou aqui. Esta é a que está
LIGADA e publicando.

`ShbMartItem.price`, do cubo do JoomPulse, **É o preço EXIBIDO** — o de
checkout, com cupom. Medido:

| item | nossa API (`productOfferV2`) | `ShbMartItem.price` | a página mostrava |
|---|---|---|---|
| 16892189215 | R$ 689,99 | **611,80** | R$ 611,80 |
| 23598844177 | R$ 599,00 | **523,48** | R$ 523,48 |

Confirmado em mais 8 itens: sempre 5% a 14,5% abaixo do nosso.

**Por que ela é melhor que a sétima**, que é a do navegador (ver "A sétima rota
EXIGE SESSÃO", logo abaixo, para o que a trava):

| | sétima (navegador) | oitava (cubo) |
|---|---|---|
| exige sessão logada na Shopee | **sim** — e é isso que a trava | não |
| arrisca a conta do dono | sim (automação de perfil logado) | não |
| custo por item | uma página de navegador, 10 a 30 s | **1/100 de uma consulta** |
| frescor | vivo, do segundo | foto por item, mediana de 3 dias |
| ancoragem | a frase da própria página | o preço vivo da API, por distância |
| estado | desligada, e toda leitura falha fechada | **ligada** |

A sétima continua sendo a MELHOR quando existir uma sessão que possa ser usada
sem risco: ela lê a página viva e ancora o número na frase que o qualifica. Por
isso, no pipeline, **ela vence**: `shopee_checkout` só preenche o que
`preco_real` não carimbou.

### O que a coleta rende, e o que ela custa

`ShbMartItem` tem **uma linha por item** e o teto do Cube.js é de 100 linhas por
consulta, então cabem **100 itens por consulta** (medido em 2026-08-29: 120 ids
no filtro devolveram exatamente 100 linhas; `offset: 100` devolveu as 17
restantes). Compare com o cubo de histórico da 5O, cujo grão é o INTERVALO e
onde cabem ~4 itens.

Com a cota diária de ~9 consultas, **1 consulta/dia basta**: ela cobre os 100
primeiros da fila que o pipeline publicaria, contra ≈60 posts/dia. E rodar mais
de uma vez por dia **não adianta**: a data que a guarda usa é
`itemLastSeenDate`, o dia em que a Shopee foi raspada pelo JoomPulse, não o dia
da nossa consulta.

A cobertura do cubo não é censo (só itens com ao menos uma venda na vida,
categoria resolvida e preço abaixo de R$ 50.000): **117 dos 120** ids do topo
da nossa fila estavam lá.

### A guarda: falhar fechado, com os números que a escolheram

O preço da API é VIVO — o `refresh_price` roda segundos antes de publicar. O do
cubo tem dias. O do cubo só vai à peça quando passa por três portas; fora delas
o post publica o preço da API, exatamente como antes desta fase.

Medido em 2026-08-29 sobre **117 itens do topo da fila real**, o preço do cubo
contra o `price_current_cents` do estoque:

- **105 tinham o cubo menor**; 12 tinham-no maior ou igual — esses não são
  desconto nenhum, são preço velho de anúncio que barateou;
- dos 105, **85 ficam em até 15%**, e eles se empilham nos degraus de cupom da
  Shopee: ~5%, ~10% e **exatamente 14,50%**;
- há um **cotovelo claro** entre 14,57% e 15,34%. Acima dele os valores são
  espalhados (16,13 · 16,99 · 17,77 · 18,09 · 18,27 · 19,19 · 19,54 …) e a
  cauda vai a **54,70% e 55,84%** — R$ 199,90 virando R$ 88,27 não é cupom, é o
  preço tendo mudado desde a raspagem.

| porta | limiar | por que este número |
|---|---|---|
| é menor | cubo `<` vivo | preço do cubo maior é preço velho, não cupom |
| não longe demais | ≤ **15%** abaixo | o topo do degrau de cupom mais alto que sabemos existir, logo antes do cotovelo |
| não perto demais | ≥ **1%** abaixo | menos que isso é ruído de arredondamento de uma medida `avg`; nenhum dos 117 foi excluído por ele (o menor gap positivo foi 1,45%) |
| recente | raspagem há ≤ **3 dias** | ver abaixo |

**A idade NÃO é de 24 h**, ao contrário do que o enquadramento inicial supunha.
O cubo é reconstruído todo dia, mas com a última raspagem de CADA item: na
amostra, `itemLastSeenDate` teve **mediana de 3 dias e máximo de 30**, e só 36
dos 117 tinham sido vistos nas últimas 24 h. Por isso a idade é guarda de
primeira classe, e não uma nota de rodapé.

**Resultado medido: 50 dos 117 publicariam o preço de checkout hoje.** Os 67
que caem são, quase todos, por idade (56 deles) — 36 estão a exatamente 4 dias,
um dia além do teto. Subir `idade_max_dias` para 4 levaria a cobertura de 50
para 86; é um botão do `config.yaml`, com o número ao lado, e é decisão do dono.
O teto de 3 foi escolhido por ser o mesmo frescor que o projeto já aceita para
uma candidata (`shopee.candidate_max_age_days`), e não por ajuste à amostra.

### Onde isso mora

- **A conta e as guardas**: `src/afiliado/shopee_checkout.py`, com
  `tests/test_shopee_checkout.py`. Sem rede.
- **O leitor do bruto**: `src/afiliado/joompulse.py`. O conector devolve
  **colunar** (`columns` + `data` de listas), e não a lista de dicionários que a
  fase 5O supôs — corrigido lá também, onde um bruto colunar virava "sem linha
  no cubo" para todo item.
- **O dado**: seção `checkout_prices` de `data/watchlist.json`, ao lado de
  `price_refs`/`price_floors`. É FATO datado: sobrevive ao vencimento da
  opinião e carrega em `measured_at` o dia da RASPAGEM.
- **A coleta**: skill `/shopee-checkout-refresh`.
- **O interruptor**: `preco_checkout:` no `config.yaml`, com os três limiares.
- **O aviso**: seção vazia ("a coleta nunca rodou") ou sem nenhum preço dentro
  do teto de idade viram aviso diário ao chat de operações, e o `afiliado
  doctor` tem um item `preco_checkout` que conta quantos ainda valem. Ele
  **nunca** pinta o doctor de vermelho: nada quebra quando falta o dado — o
  post publica o preço da API —, e um ❌ num sistema que está entregando é a
  maneira mais rápida de ensinar o dono a ignorar o ❌ (é o critério do data
  feed da 5L).

### A porcentagem na arte (o pedido do dono)

> "mesmo que o joompulse traga o preço correto, precisamos evidenciar a
> porcentagem de desconto nos stories, pois isso atrai a atenção do consumidor"

**Qual porcentagem:** a de CHECKOUT — entre o preço de catálogo
(`price_current_cents`, o que a API dá) e o preço exibido
(`price_checkout_cents`). No caso real: 599,00 → 523,48 = **−12%**. São dois
números observados por NÓS, e é isso que a torna publicável. `priceDiscountRate`
e qualquer desconto derivado de `price_original_cents` continuam PROIBIDOS sem
o veredito — é o "de" inflado que este projeto inteiro existe para não
certificar.

O buraco que os previews acharam: uma peça de **modo B** com preço de checkout
não mostrava porcentagem NENHUMA. O badge de −N% só existia no modo A, e o modo
A é raro. Desde a 5R:

- **uma porcentagem por peça, sempre a mais forte que é verdadeira.** Modo A: a
  do veredito (referência → preço publicado), que é sempre maior que a de
  checkout porque a referência é maior que o catálogo. Modo B: a de checkout;
- **a base junto.** No modo B com leitura, a pill ganha o preço de catálogo
  RISCADO. Porcentagem sem base é um número que o seguidor não pode conferir — e
  este riscado não é o "de" do vendedor: é a nossa medição de hoje, o mesmo
  número que a página escreve como "ou R$ 599,00 sem cupom";
- o **selo** de modo A e o badge coexistem sem colidir; conferido nas imagens.

Geometria medida em 2026-08-29 (46 peças em `.claude/previews/fase5r/`: story e
feed, modo A e B, com e sem selo, com e sem leitura, condição curta e longa,
título longo, teto de preço, story no modo figurinha, ML):

| pill de modo B, pior caso publicável (R$ 999,99 → R$ 899,99) | story (útil 936) | feed (útil 952) |
|---|---|---|
| "COM CUPOM" — o que o cubo produz | 886 (riscado inteiro) | 787 (riscado inteiro) |
| "NO PIX COM CUPOM" — só a leitura do navegador produz | 813 (o riscado SAI) | 899 (riscado inteiro) |

E a rede da 5P continua de pé: a referência que não cabe **sai inteira**. O
"R$ 7…" não voltou por esta porta.

## O preço com cupom não vem por SERVIDOR — seis caminhos fechados

(Pelo navegador ele vem, mas só COM SESSÃO: ver "A sétima rota FUNCIONA" e, logo
depois, "A sétima rota EXIGE SESSÃO" — a medição da fase 5P, que é a que decide
se dá para usá-la. Estas seis continuam valendo como o que NÃO adianta tentar
por HTTP puro.)

Cinco pela API de afiliados, todos medidos nesta data:

1. **`productOfferV2`, todos os 26 campos** (introspecção do schema): os únicos
   de preço são `price`, `priceMin`, `priceMax` e `priceDiscountRate`. Para o
   item do caso, os quatro diziam 689,99 / 689,99 / 759 / 47%.
2. **As 8 queries que a API expõe** (`shopOfferV2`, `shopeeOfferV2`,
   `productOfferV2`, `conversionReport`, `validatedReport`,
   `partnerOrderReport`, `listItemFeeds`, `getItemFeedData`): nenhuma tem
   argumento ou retorno de cupom/voucher.
3. **O data feed de itens** (`getItemFeedData`): 16 colunas, das quais só
   `price`, `sale_price` e `discount_percentage` são de preço. `sale_price`
   difere de `price` em 48 de 100 linhas amostradas, e a diferença bate com
   `discount_percentage` — ou seja, é o par "de/por" que o `productOfferV2` já
   dá, não o preço com cupom.
4. **A rota pública do site** (`/api/v4/item/get`): **HTTP 403** a partir do
   servidor.
5. **A própria página** só entrega o número renderizando JavaScript, atrás de
   proteção anti-bot.

E a sexta, medida em **2026-08-28** antes de aceitar a decisão da fase 5N:

6. **O link de afiliado resolvido até o destino.** No Mercado Livre esse
   caminho REVELOU o preço — foi assim que a fase do ML descobriu que
   publicávamos o preço de um vendedor que não vence o buy box. Na Shopee não:
   `s.shopee.com.br/...` cai numa variante mobile
   (`/opaanlp/{loja}/{item}?__mobile__=1`) de **277 KB** cujo JSON embutido não
   tem **nenhum** campo de preço. Os "611" e "689" que aparecem no HTML são
   hashes de build e flags, não preços.

**Conclusão sobre a API: é estrutural.** Desconto de meio de pagamento e cupom
acontece no checkout e não existe no catálogo de afiliados. Nenhuma das seis
rotas acima resolve, e nenhuma vai resolver: só a Shopee, expondo o campo.

> **Esta conclusão continua certa sobre a API de afiliados, e ERRADA sobre o
> mundo.** Ela olhava só para os nossos próprios acessos. Quem já raspa a
> página da Shopee todo dia é o JoomPulse, e o preço que ele guarda é o
> EXIBIDO — ver "A OITAVA rota RESOLVE", no topo. O erro de método: seis
> caminhos fechados viraram "não existe caminho", quando o que estava provado
> era "não existe caminho POR AQUI".

## A sétima rota FUNCIONA — e ela derruba a conclusão acima

As seis rotas eram todas de **servidor**. O dono insistiu na pergunta e estava
certo: **num navegador de verdade, que executa o JavaScript, o preço aparece.**
Medido em 2026-08-28 lendo o DOM da página do item 16892189215:

```
R$611,80
ou R$689,99 sem cupom em outros métodos de pagamento
Até R$40,00 de desconto no frete com cupom
```

Parseável, com o número E a condição. O bloco de preço chega VAZIO no primeiro
render e leva de 10 a 14 s para preencher — exige laço de espera, não um
`get` só.

**O que isso muda no enquadramento**, e é a parte que importa: o rótulo nunca
foi o problema. O nosso era a versão NEGATIVA do mesmo fato.

| o que dizemos | como soa |
|---|---|
| "R$ 689,99 sem cupom" | limitação — afasta |
| "R$ 611,80 no Pix com cupom" | oferta — atrai |

Mesma honestidade, força oposta. E o segundo é literalmente a frase da página.

**O que custa, e por que ainda não foi feito:**

- as tarefas rodam em sessão NÃO interativa desde a fase 5I (para tirar a
  janela da tela do dono) — sessão sem desktop não roda Chrome normal;
- o navegador da máquina está **logado** como o dono. Carregar 60 páginas
  automatizadas por dia de um perfil logado é o padrão que a Shopee caça, e
  perder a conta encerra o lado Shopee inteiro — que hoje é 100% do que
  publicamos. Perfil separado e deslogado é requisito, não detalhe;
- ler `innerText` procurando "ou R$ X sem cupom" depende da redação da Shopee:
  se ela mudar, tem de **falhar fechado** (publicar o preço da API, como hoje)
  e nunca adivinhar.

### A sétima rota EXIGE SESSÃO — e é isso que a trava (fase 5P, 2026-08-28)

A leitura acima foi feita no **Chrome logado do dono**, que é exatamente o
perfil que o requisito acima proíbe usar. Com um perfil PRÓPRIO E DESLOGADO a
página do produto não entrega preço nenhum. Medido nesta data, com o binário do
Chrome instalado e um `--user-data-dir` novo:

| tentativa | resultado |
|---|---|
| headless, ida direta a `/product/{loja}/{item}` | "Login Necessário" |
| headless, aquecendo pela home antes | "Login Necessário" |
| **com janela** (headless=False), aquecendo pela home | "Login Necessário" |
| URL canônica `…-i.{loja}.{item}`, desktop | "Login Necessário" |
| user agent e viewport de **celular**, nas duas formas de URL | "Página indisponível" |
| `?__mobile__=1` | "Página indisponível" |
| home → `/search?keyword=…` → clicar no card | a **home** também caiu |

O endereço final de todas elas é `https://shopee.com.br/verify/traffic/error`:
não é uma tela de login, é a **verificação de tráfego** da Shopee. E ela
PIORA com o uso — as primeiras tentativas carregaram a home normalmente e,
depois de ~10 requisições automatizadas, a própria home passou a cair no
interstício. Ou seja: navegar automatizado desta máquina **queima a reputação
do IP que o dono usa logado**. Foi por isso que a medição parou aí.

Duas rotas NÃO foram sondadas, de propósito:

- **abrir o link de afiliado (`s.shopee.com.br/…`) no navegador.** Isso é um
  clique de afiliado artificial — a fase 5A tirou o GET do `check_link`
  justamente para não fazer isso (tráfego inválido é risco de encerramento da
  conta de afiliados). E o interstício é aplicado ao NAVEGADOR, não à URL: a
  rota 6 acima já mostrou que o destino é a casca mobile sem campo de preço;
- **logar um perfil separado com a conta do dono.** É o mesmo cookie de
  sessão, no mesmo IP, com automação em cima — o risco que a guarda existe
  para evitar, só que com um passo a mais.

**O que sobra, e é decisão do dono:** uma conta Shopee **descartável**, logada
num perfil só nosso. Aí a rota funciona e o que se arrisca é uma conta que não
sustenta nada. Nada disso está feito — o que está feito é a máquina, pronta e
segura, para o dia em que existir essa sessão.

### O que a fase 5P entregou, com a leitura DESLIGADA

`src/afiliado/preco_real.py`, `preco_real:` no `config.yaml` (com
`enabled: false`) e `afiliado preco-real <url|itemId>`.

- **`parse_preco`** só devolve número quando a frase "ou R$ X sem cupom" está na
  página E o X é exatamente o preço que o `refresh_price` mediu segundos antes
  (a âncora). Toda outra situação é uma recusa COM NOME — interstício, frase
  ausente, preço de outro anúncio, destaque que não é menor, destaque barato
  demais (parcela lida como preço) — e a recusa é o comportamento de hoje.
- **A condição publicada é "com cupom"**, não "no Pix com cupom": o `innerText`
  medido não traz a palavra Pix. Ela só entra se a própria página a escrever
  entre o número e a frase.
- **O preço de checkout NÃO substitui o de catálogo.** Ele é carimbado ao lado
  (`Offer.price_checkout_cents`). O de catálogo continua sendo a série do
  `price_log`, da mediana, do p25, do piso do selo e do `check_price` — um preço
  de cupom ali faria a regra do quartil da 5B alegar desconto todo dia em que
  houvesse cupom. O portão do modo A segue decidido catálogo contra catálogo; o
  percentual IMPRESSO passa a ser o dos dois números exibidos, para a conta
  fechar na peça.
- **O desarme** vive em `day_flags` (como o `instagram_story_link`): 3 leituras
  seguidas sem preço fecham o dia; o interstício fecha na primeira.
- **O perfil** passa por `preco_real.perfil_proibido` — qualquer caminho dentro
  do "User Data" do Chrome/Chromium/Edge do usuário é recusado, e o run avisa o
  chat de operações em vez de subir.

**A dependência é o Playwright**, extra opcional `preco` (`pip install -e
.[preco]`). Não é CDP contra o Chrome já instalado: com CDP alguém precisa subir
o Chrome à mão com `--remote-debugging-port` e `--user-data-dir`, e o erro de
apontar esse segundo argumento para o perfil do dono — ou de anexar à instância
já aberta — é justamente o que encerra o lado Shopee.
`launch_persistent_context` recebe o diretório como argumento obrigatório e ele
passa pela guarda antes: a regra vira código. `channel: chrome` usa o binário
instalado (menos detectável) com um perfil vazio nosso, então nem o download do
Chromium é necessário. Sem o extra, a leitura se desarma com um aviso acionável
e o pipeline segue inteiro.

**O rótulo, invertido.** Ele NÃO é o `MOSTRAR_SEM_CUPOM` religado — aquele é a
versão negativa e o dono a recusou com razão. Este só existe quando há número
real e diz o que ele exige. Mesmo slot da 5K (à direita do preço, dentro da
pill, zero altura), e a geometria foi conferida nos previews de 2026-08-28
(`.claude/previews/fase5p/`, 24 peças: story e feed, modo A e B, com e sem selo,
com e sem leitura, condição curta e longa, caso real e teto de preço):

| pior caso publicável (R$ 999,99, riscado R$ 1.999,99) | story | feed |
|---|---|---|
| sem rótulo | 720 px | 637 px |
| "COM CUPOM" (162/144 px — a MESMA largura de "SEM CUPOM") | 906 | 803 |
| "NO PIX COM CUPOM" (288/256 px) | 929 | 915 |
| largura útil | 936 | 952 |

**E o preview mudou o código.** Com a condição longa no pior caso, o guarda
horizontal cortava a referência riscada em **"R$ 7…"** ao lado de "R$ 523,48" —
um número pela metade, riscado, que se lê como "de R$ 7". Preço truncado não é
decoração degradada, é informação falsa. Desde esta fase a referência que não
cabe **inteira sai inteira**; `_hard_truncate` continua servindo aos títulos,
onde um corte não mente. Sem ela a peça ainda diz o desconto: o badge de -N%
continua lá.

## O que fazer, então

O que **não** fazer: publicar um preço com cupom que não conseguimos verificar,
ou omitir o preço. Prometer menos do que a realidade e o seguidor achar mais
barato é o erro seguro; o contrário destrói a única coisa que a conta vende.

Sobram duas saídas, e a fase 5K tomou uma e a 5N tomou a outra: **rotular** o
preço com "sem cupom", ou **publicar o número sem ressalva**. As duas são
honestas — R$ 689,99 é o preço que qualquer um paga sem cupom, e a peça nunca
afirmou ser o menor jeito de pagar. É diferente do caso do ML, onde o número
publicado não era o de ninguém.

### O rótulo EXISTE e está DESLIGADO (fase 5N, 2026-08-28)

O interruptor é `pricing.MOSTRAR_SEM_CUPOM`, hoje `False`. **Razão do dono**,
textual:

> "o que importa para o usuário é se o produto está com o desconto, e ver isso
> atrai automaticamente; produto classificado como 'sem cupom' não atrai em
> nada"

O que se ganha e o que se perde, para quem for reavaliar:

- **compra**: o seguidor que abre o anúncio e vê um preço MENOR entende por
  quê, em vez de concluir que o nosso número está velho (foi o que aconteceu no
  caso que abre este runbook);
- **custa**: é uma ressalva colada ao preço, e pesa mais justamente na peça
  mais fraca — a de modo B, que não tem desconto para mostrar.

**Risco que fica ligado:** o seguidor pode achar o produto mais barato do que o
post diz. Erro para o lado que não frustra.

**Como religar:** `MOSTRAR_SEM_CUPOM = True` em `src/afiliado/pricing.py`, e só
isso — a colocação, os tamanhos e as folgas medidas na 5K continuam no código e
seguem valendo. A suíte cobre os DOIS estados (fixture `rotulo` em
`tests/conftest.py`), então religar não deixa nenhum teste vermelho.

### Como o rótulo é feito, quando está ligado (fase 5K, 2026-08-28)

A regra mora em `pricing.sem_cupom` — **só Shopee**, porque o preço que
publicamos do ML é o do anúncio que vence o buy box, exatamente o que a página
mostra. Dali ela viaja para tudo que publica preço, sem ninguém reimplementá-la:

| superfície | onde aparece |
|---|---|
| arte de story, de feed e slide do carrossel | `creative._pill_nota`: dentro da pill, à direita do preço |
| texto do Telegram | `pricing.price_line_html` |
| legenda do feed e do despacho de story | `pricing.price_line` |
| legenda do carrossel | `pricing.preco_publicado` |

**Por que dentro da pill.** A colocação foi decidida gerando previews e olhando
as imagens (story e feed, modo A e B, com e sem selo, título longo, story com
figurinha de link). As três alternativas caíram na imagem:

- **na linha de meta**: ela não tem guarda horizontal (fase 5H). Com o rótulo,
  o caso típico da Shopee vai de 840 px para 1056 px no story — margem de 12 px
  contra 72 de padding —, e a 1,5 milhão de vendas sai a 1146 px, cortada pela
  borda. Além disso, no feed em modo A com selo a meta é derrubada pelo guarda
  de overflow, e o rótulo sumia junto;
- **em segunda linha dentro da pill**: engorda a pill ~50 px e o guarda passa a
  derrubar o **selo** no feed com título longo;
- **em linha própria sob a pill**: come o respiro `STORY_META_GAP = 88`, que o
  dono pediu.

Alinhado pela linha de base do preço, o rótulo custa **zero altura** e cabe no
guarda horizontal que a pill já tinha. Pior caso de LARGURA (preço de quatro
dígitos, R$ 999,99, com referência riscada de R$ 1.999,99 — folga deliberada: a
fase 5S baixou `price_max_brl` para 150, e a arte não pode voltar a quebrar se
alguém subir o teto de novo): pill de 906 px
no story — 30 px dentro da largura útil, 15 px de margem sobrando; 803 px no
feed. Onde não couber, quem cede é a referência riscada, nunca o rótulo.

**E desligado, a pill não fica com buraco.** É o mesmo mecanismo: rótulo vazio
faz `nota_bloco = 0` e a pill volta a medir preço + padding, a geometria de
antes da 5K. Medido nas 21 peças da 5N (story e feed, modo A e B, com e sem
selo, título longo, teto de preço, ML, story com figurinha) — as mesmas 906/803
px do pior caso caem para 720/637. Nenhuma pill descentrada, nenhum respiro
sobrando à direita do preço.

## Achado colateral, e ele é grande

`listItemFeeds` / `getItemFeedData` expõem **feeds de catálogo inteiros**:

| feed | itens |
|---|---|
| Shopee Brasil (FULL) | 10.000 |
| **Shopee Oficial BR (FULL)** | **100.000** |
| Shopee Brasil (DELTA) | 19.756 |
| Shopee Oficial BR (DELTA) | 170.217 |

Cada linha traz `itemid`, `title`, `price`, `sale_price`,
`discount_percentage`, `item_rating`, categorias globais, `image_link`,
`product_link` e o link curto. O teto por chamada é **500** (medido: 1000
devolve `error [11001] ... the maximum limit is 500`), então o catálogo oficial
inteiro sai em **200 chamadas**.

Compare com a descoberta de hoje: 8 chamadas por run dentro de janelas de 2.000
itens por (categoria, ordenação). O feed é uma superfície muito maior e mais
barata — vale uma fase própria, tanto para volume quanto para aliviar a pressão
sobre a API de busca.

### Feito (fase 5L, 2026-08-28)

O feed entra **ao lado** da busca, em `ShopeeSource._fatia_do_feed`, com as
mesmas disciplinas dela: `_post`, teto de chamadas por run
(`shopee.feed_calls_per_run`), cursor de `offset` persistido e `SourceError`.

**FULL, e não DELTA — o contrário do que parecia.** O DELTA oficial tem
**170.217** linhas contra 100.000 do FULL (341 chamadas contra 200) e, numa
janela de 500 dele, **229 linhas são `DELETE`** contra 264 `NEW` e 7 `UPDATE`.
Ou seja: o DELTA custa 70% mais chamadas para entregar menos linha
aproveitável. Não existe, nesta conta, o caminho barato de manter o estoque
fresco pelo delta.

**O `datafeedId` carrega a data** (`428536169534861312_FULL_2026-08-27`) e o
arquivo é regerado todo dia, então ele é relistado a cada run (1 chamada, além
da janela). O ciclo de 200 janelas é, por isso, uma **amostra rotativa** do
catálogo — não uma partição dele.

**O que o feed não traz: `commission` e `sales`.** As duas só existem depois do
`refresh_price`, que roda imediatamente antes de publicar. Com o `min_ev_brl`
do config real, o piso de EV leria a comissão ausente como "vale zero" e
mataria 100% das candidatas do feed em silêncio — o mesmo defeito da 5J com
outro campo. A rede é `selection.comissao_desconhecida` mais um lote inteiro de
feed em `tests/test_zero_silencioso.py`.

**O que rende, medido em 2026-08-28.** Pelas mesmas 200 chamadas:

| superfície | itens varridos | elegíveis | por chamada |
|---|---|---|---|
| busca (5 raízes × 40 páginas × 50) | 10.000 | 5.460 (54,6%) | ~27 |
| data feed oficial (200 × 500) | 100.000 | ~32.000 (32,2/32,4/32,6% em três janelas) | ~160 |

E o que ele **não** rende: popularidade. Os itens do feed são a cauda longa do
catálogo — mediana de **1 venda** em 30 dias no feed oficial e **9** no "Shopee
Brasil" (25 itens sorteados de cada), contra os milhares do topo de categoria
que a busca traz ordenada. O feed é **alcance e reserva**, não substituto: sem
comissão e sem vendas ele entra no fim da fila e publica quando o topo se
esgota, que é o papel dele.

**Por curtidas, não por nota.** A nota do feed é 5,0 na mediana (165 de 172
linhas acima de 4,5 — não separa nada). O `like` separa: numa janela de 500 do
feed oficial, as 12 linhas mais curtidas somam **2.152** vendas de 30 dias
(mediana 33) e as 12 menos curtidas somam **2** (mediana 0). No "Shopee Brasil"
a coluna vem zerada em todas as linhas — é mais uma razão para o feed oficial.

**O teto de `feed_keep_per_run` não é da API, é do `state.db`.** 32% das linhas
passam nos portões; guardar tudo seriam ~9.800 candidatas/dia e, com
`candidate_max_age_days: 3`, ~29 mil linhas de ~600 bytes num arquivo binário
versionado — contra 60 posts/dia. Com 10, são ~610/dia.

**O `product_short link` já é de afiliado** (`utm_medium=affiliates`, 500 de
500 linhas) e vira o `offer_link` da oferta — a queda do `generateShortLink`.
Ele **não** vira o link publicado: é uma URL de ~700 caracteres contra os ~30
de um `shope.ee`, e trocar o gerador oficial por ela mexeria na atribuição de
todo post da loja por 60 chamadas/dia.
