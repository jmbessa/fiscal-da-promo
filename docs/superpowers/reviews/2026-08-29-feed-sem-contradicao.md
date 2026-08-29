# O feed sem contradição — o que a conta pode postar sem se desmentir

Pesquisa fechada em **29/08/2026**. Escopo: feed do Instagram (posts, carrosséis
e Reels). Motivo: a bio promete *"Confiro o preço dos últimos 90 dias. Se o
desconto é falso, eu não posto"* e as duas peças que a fase 5D entregou fazem o
contrário — o **Termômetro** anuncia na capa quantas ofertas do próprio álbum
reprovaram, e o **Flagrante** é um post *sobre* uma promoção falsa.

Esta pesquisa **não repete** a de 27/08
([`2026-08-28-pesquisa-feed.md`](2026-08-28-pesquisa-feed.md)). O que estiver lá
— benchmarks de formato, cadência, *sends* vs *saves*, engagement bait, CONAR —
é premissa aqui. O que segue é o que **muda**.

## Como ler a força da evidência

Mesma escala do documento anterior:

| Marca | Significa |
|---|---|
| **FORTE** | Declaração oficial da plataforma **ou** estudo com amostra grande e metodologia declarada (n, período, fonte) |
| **MÉDIA** | Fornecedor único sem método completo, veículo confiável citando estudo, jurisprudência resumida por fonte secundária, **ou observação direta e datada de um perfil** |
| **ANEDÓTICA** | Blog de agência/ferramenta sem fonte primária rastreável, caso único, ou afirmação que não confirmei na origem |
| **SEM EVIDÊNCIA** | Hipótese plausível. Não é fato, e está escrito |

Onde a conclusão é minha leitura conciliando fontes, está dito **(leitura minha)**.

---

## 1. A restrição que não pode ser violada

Em **30/04/2026** o Instagram estendeu a regra de originalidade — que já valia
para Reels — a **fotos e carrosséis**: conta que posta majoritariamente conteúdo
que não criou ou não editou "de forma material" deixa de ser elegível para
recomendação a não-seguidores (feed de recomendados, Explorar, aba Reels).
Seguidores continuam vendo.
https://techcrunch.com/2026/04/30/instagram-restricts-reach-of-content-aggregators-in-new-crackdown/ ·
https://www.engadget.com/2160560/instagrams-recommendation-algorithm-will-penalize-unoriginal-photo-and-carousel-posts/ ·
https://www.tubefilter.com/2026/04/30/instagram-removes-algorithm-recommendations-repost-content-aggregator/
— **FORTE**

Isso já estava no plano. **Três coisas novas**, que mudam decisão:

**1.1. A Meta nomeia "gráficos" como caminho de originalidade.** A orientação
publicada é adicionar "unique edits, narration, **or graphics**" ao conteúdo
encontrado; "low-effort edits" como marca d'água e crédito ao autor **não
bastam** (Engadget, 30/04/2026 — **FORTE**). O `render_grafico_preco` não usa a
foto do vendedor: é arte 100% nossa, e é o formato que a própria orientação cita.

**1.2. O elo fraco do pipeline não é o gráfico — é o carrossel.** Cada slide do
Termômetro é `_render_slide_oferta`: **foto do vendedor** com pill de preço,
contador e cabeçalho por cima. Isso é exatamente a silhueta do conteúdo punido
(foto alheia + etiqueta), com uma camada de marca em cima. O gráfico está do
lado seguro da linha; o carrossel de fotos está **na** linha. Nenhuma fonte diz
onde a Meta corta — a leitura de que o slide de foto é mais arriscado que o
slide de gráfico é **minha (leitura minha, SEM EVIDÊNCIA de limiar)** —, mas a
assimetria é gratuita de corrigir: um mini-gráfico por slide desloca a peça para
o lado da orientação escrita.

**1.3. Dá para PARAR de adivinhar.** O Instagram expandiu o hub **Account
Status** para contas profissionais dizerem se o conteúdo está elegível a
recomendação a não-seguidores ou se viola as Recommendations Guidelines
(cobertura de 30/04/2026 — **FORTE** quanto à existência da ferramenta). Custa
zero e é a única medição direta disponível deste risco. **Recomendação
operacional: conferir o Account Status antes e depois de qualquer mudança de
formato**, e registrar a leitura. É a diferença entre saber e supor.

---

## 2. Onde exatamente está a contradição — e a invariante que a resolve

A bio faz uma promessa sobre **o que entra no feed**. A régua (`pricing.verdict`)
produz dois estados: **modo A** (a peça pode alegar desconto verificado) e
**modo B** (não pode — sem referência, sem p25, janela curta ou desconto abaixo
do piso), mais o **selo** de mínima histórica, que é independente.

O Termômetro monta o álbum com a régua do `run` — que **inclui modo B** — e
depois a capa confessa a conta: `capa_do_termometro` produz "5 OFERTAS. 3
PASSARAM." e, no pior caso, "NENHUMA DAS N PASSOU." A peça, portanto, **publica
o que ela mesma diz que não passou**. Essa é a contradição literal, e ela está
na seleção, não na arte.

A invariante que eu proporia, e que é testável em código:

> **Nenhuma peça do feed pode ter como assunto uma oferta que a régua reprovou,
> nem levar o leitor até ela.**
> Traduzindo: se `verdict.mode == "B"` e não há selo, o produto **não aparece**
> em peça de feed — nem como slide, nem como título, nem na legenda.

O que sobra é grande, e se divide em três territórios que **não** violam a
invariante:

| Território | Assunto da peça | Nomeia produto? |
|---|---|---|
| **A prática** | O truque de precificação, o direito do consumidor, o "metade do dobro" | Não |
| **O método** | Como a régua funciona, o que é "preço de sempre", como ler um gráfico | Não |
| **O aprovado** | A oferta que passou, com a prova desenhada | Sim |

Tudo o que esta pesquisa recomenda cabe nessas três caixas.

### 2.1. A bio também precisa ser verdadeira

Se a regra é "o feed não contradiz a bio", a bio tem de ser conferível. Hoje há
**três números diferentes para a mesma promessa**:

- a bio citada pelo dono diz **"últimos 90 dias"**;
- `docs/brand-guidelines.md` §6 traz a bio oficial com **"histórico de 12
  meses"**;
- o código exige `price_window_days >= MIN_WINDOW_DAYS`, que é **14**
  (`pricing.py:40`); 90 é só a janela-alvo de referência
  (`DEFAULT_REF_WINDOW_DAYS`), e as peças imprimem o **N medido**.

Uma peça honesta que diz "verificado nos últimos 21 dias" **desmente a bio que
promete 90**. É a mesma classe de erro que a conta existe para apontar, e é de
graça consertar: a bio deve dizer o que o código faz ("confiro o histórico de
preço de cada oferta antes de postar; se o desconto não se sustenta, ela não
entra"), sem número fixo, ou com o número que o portão realmente exige.

---

## 3. O achado que resolve o enquadramento: acuse a prática, aprove o produto

Existe **um precedente brasileiro** que faz exatamente as duas coisas que o dono
acha incompatíveis, e faz na mesma bio.

**@desrotulandoapp — 895 mil seguidores** (50 seguindo), bio:
> "Não seja alvo fácil da indústria · **Compre com confiança** · Alcance uma
> alimentação mais saudável · 📲 +5M de downloads"

Observação direta em 29/08/2026, https://www.instagram.com/desrotulandoapp/ —
**MÉDIA**. O app dá nota de 0 a 100 a alimentos industrializados com base no Guia
Alimentar do Ministério da Saúde; o perfil publica Reels analisando produtos de
marca e carrosséis comparando itens.
Contexto do app: 2 milhões de usuários e 20 mil itens avaliados já em 2019
(https://ojoioeotrigo.com.br/2019/02/desrotulando-o-app-que-virou-sucesso-se-inspira-no-guia-alimentar/)
— **MÉDIA**.

A estrutura da bio é a resposta à pergunta do dono: **a acusação é dirigida à
indústria (uma classe), e o que se entrega ao seguidor é permissão para
comprar.** Não há contradição porque o objeto acusado e o objeto aprovado não
são o mesmo.

O segundo precedente, no registro de acusação puro:
**@doutorfran (Francisco Rabello) — 1,3 milhão no Instagram** (observação direta,
29/08/2026), 4,3 milhões no TikTok, advogado de **direito do consumidor** que
publica vídeos curtos com humor mostrando situações em que o direito do
consumidor é violado. Wikipédia pt e ranking de perfis jurídicos (Secco Attuy,
jan/2025, 29 perfis) o colocam em 5º entre advogados brasileiros no Instagram.
https://pt.wikipedia.org/wiki/Doutor_Fran ·
https://frankdave.com.br/ranking-dos-advogados-brasileiros-mais-influentes-no-instagram/
— **MÉDIA**

E o contraste que importa, porque é o lado que a conta está tentando ocupar:

| Perfil | Seguidores | O que é |
|---|---|---|
| @doutorfran | **1,3 M** | Personagem que ensina a não ser enganado |
| @desrotulandoapp | **895 mil** | Veredito por método público + "compre com confiança" |
| @reclameaqui | **277 mil** | Instituição de reputação, marca enorme fora do Instagram |
| @pelandobr | 33 mil | Maior base de histórico de preço do país (doc anterior) |
| StatMuse | 44 mil | Cards de dado gerados por programa (doc anterior) |

Observações diretas de 29/08/2026 para os três primeiros
(https://www.instagram.com/reclameaqui/ · 277 mil, bio "Sua voz melhora empresas
pra você") — **MÉDIA**.

**A leitura (leitura minha, MÉDIA):** no Brasil, nesta vizinhança, *instituição
que publica veredito* fica na casa das dezenas/centenas de milhares; *personagem
que ensina a não ser enganado* chega ao milhão. Dado publicado sem narrativa é o
degrau mais baixo.

**E a contra-leitura, que precisa ficar junto:** os dois casos grandes têm
confundidores que a conta não tem. O Desrotulando chega ao Instagram com **5
milhões de downloads de app** — é o padrão "audiência prévia" que
`docs/crescimento.md` já identificou como origem de todos os grandes do nicho.
O Doutor Fran tem rosto, humor de esquete e hoje é candidato. **Nenhum dos dois
prova que um feed anônimo e gerado cresce do zero.** O que eles provam é mais
modesto e ainda assim útil: *o registro "não seja enganado + compre com
confiança" tem público de milhão no Brasil*.

---

## 4. Negatividade: a evidência é forte, e é contestada

O dono está propondo trocar acusação por aprovação. Vale saber o preço disso.

**A favor da acusação:**

- **Robertson et al., *Nature Human Behaviour* (2023)** — "Negativity drives
  online news consumption": ~105.000 variações de manchete, 22.743 experimentos
  randomizados, >370 milhões de impressões e 5,7 milhões de cliques no Upworthy.
  Cada palavra negativa a mais numa manchete de tamanho médio **aumenta o CTR em
  2,3%**; palavras positivas **reduzem ~1,0%**.
  https://www.nature.com/articles/s41562-023-01538-4 (resumo institucional dos
  autores: https://www.uni-giessen.de/en/faculties/f02/faculty/professorships/business/data-science-digitization/news/nhb_2023)
  — **FORTE**
- **Watson, van der Linden, Watson & Stillwell, *Scientific Reports* (set/2024)**
  — "Negative online news articles are shared more to social media": 95.282
  artigos (2019–2021; Daily Mail, Guardian, NYT, NY Post) cruzados com
  **579.182.075 posts** de Facebook e Twitter. Usuários têm **1,91× mais
  probabilidade de compartilhar** link de notícia negativa.
  https://www.nature.com/articles/s41598-024-71263-z ·
  https://www.jbs.cam.ac.uk/2024/why-social-media-users-like-sharing-negative-news/
  — **FORTE**

**Contra:**

- **Talaga, Batorski & Wojcieszak (arXiv 2507.19300, 25/07/2025)**: 6,08 milhões
  de posts de Facebook de 97 veículos em 6 democracias (EUA, RU, Irlanda,
  Polônia, França, Espanha), jan/2020–abr/2024, classificadores multilíngues +
  modelos mistos. Só **12,6%** dos posts são negativos, e eles recebem **~15%
  menos reações e ~13% menos comentários**. Os autores dizem explicitamente que
  o efeito da negatividade "desaparece quando se considera um espectro
  suficientemente amplo de veículos".
  https://arxiv.org/html/2507.19300v1 — **FORTE quanto ao método**, com a
  ressalva de que é **preprint** não revisado por pares na data desta consulta.

**A conciliação (leitura minha, MÉDIA):** os dois lados mediram coisas
diferentes. Watson mediu **compartilhamento**; Talaga mediu **reações e
comentários**. A síntese compatível com ambos é: *negatividade compra envio e
custa curtida*. E o Instagram ranqueia com os dois — Mosseri nomeou
`watch time`, `likes/alcance` e `sends/alcance`, com *sends* pesando mais para
não-seguidor (doc anterior, **FORTE**).

**Consequência para a decisão:** abandonar a acusação **tem custo medido** — é o
único mecanismo com evidência forte de gerar envio. Mas o custo não é a acusação
em si: é o **objeto** dela. Acusar a *prática* (que o CDC art. 37 já chama de
publicidade enganosa e o Procon-SP fiscaliza) preserva o mecanismo; acusar a
*oferta que você está listando no mesmo álbum* é o que quebra a bio. **Não
apague a acusação. Mude o alvo.**

---

## 5. A demanda: suspeita não é escassa. Permissão é.

Consulta do **Procon-SP** com **329 consumidores**, divulgada em 28/11/2025
(https://viva.com.br/dinheiro/black-friday-menos-brasileiros-percebem-bons-descontos.html)
— **MÉDIA** (órgão oficial, amostra pequena, metodologia não publicada):

- **4,48%** percebem "bons descontos reais" na Black Friday;
- **58%** dizem que "nem sempre" os preços caem; **38%**, que "em geral não há redução";
- **88%** acompanham as ofertas mesmo assim, e **53%** costumam comprar;
- **76%** dos que compraram já se sentiram enganados ao menos uma vez;
- **66%** priorizam o preço final na data, contra 29% em dias comuns;
- 42,55% descobriram depois que o "sem juros" tinha taxa.

Somando: **94% dos brasileiros dizem pesquisar preço em mais de um site**
(Rank Certo + Opinion Box, "Da busca à recomendação", 04/08/2026,
https://mundodomarketing.com.br/94-dos-brasileiros-comparam-precos-e-91-6-abandonam-produtos-ao-ver-avaliacoes-negativas)
— **MÉDIA** (o veículo não publica n nem método).

E o Procon-SP recomenda aos lojistas, entre boas práticas de Black Friday,
**"aplicar o desconto sobre o menor preço dos últimos 60 dias"**, além de
monitorar preços de 100+ produtos em 10+ varejistas nos 60 dias anteriores à
data (https://www.procon.sp.gov.br/boas-praticas-para-operacoes-na-black-friday/)
— **MÉDIA**: é página oficial e o texto aparece no índice de busca do próprio
domínio, mas a página **não abriu** na leitura direta (HTTP 404 em 29/08/2026),
então não pude conferir a redação no original. Trate como recomendação do órgão,
**não** como norma legal — o que é norma é o CDC (Lei 8.078/1990, art. 37).

**A leitura estratégica (leitura minha, SEM EVIDÊNCIA direta):** o mercado está
**saturado de suspeita**. 96% já não veem desconto bom; 76% já foram enganados.
Um feed que entrega mais suspeita compete num bem superabundante. O bem escasso é
o oposto: **alguém dizer, com prova, qual delas é real** — e é exatamente isso
que o Desrotulando vende na segunda linha da bio ("compre com confiança"). O
Flagrante vende o que o público já tem. O selo vende o que ele não encontra.

Isso não é um achado publicado. É a leitura mais defensável dos números acima, e
está marcada como leitura.

---

## 6. Os formatos que sobram

Quatro, em ordem de defensabilidade. Cada um: o que é, por que atrai, o que o
pipeline já tem, esforço humano, risco.

### 6.1. "As N do dia com selo do Fiscal" — o Termômetro reenquadrado
**FORMATO RECOMENDADO. É a menor mudança com o maior efeito.**

- **O que é:** o mesmo carrossel diário, com **uma** mudança de seleção — só
  entram ofertas com `verdict.mode == "A"` **ou** com selo. A capa deixa de
  contar reprovações; passa a ser sempre o ramo que já existe no código
  (`"AS N DO DIA COM SELO DO FISCAL"`).
- **Por que atrai:** carrossel é o formato de maior *save rate* (0,05%, 9× a
  imagem única) e 36% dos brasileiros salvam para lembrar de comprar depois
  (doc anterior, **FORTE**). O que muda aqui não é o alcance — é a coerência:
  a peça passa a ser a prova da bio, e não a confissão contra ela.
- **O que o pipeline já tem:** tudo. `capa_do_termometro` já tem o ramo do selo;
  `selection` já sabe do veredito (`_desconto_publicavel`) e já tem o portão
  `require_price_ref`; `legenda_do_carrossel` já imprime o selo por item. A
  mudança é o filtro e a poda dos ramos "N PASSARAM" / "NENHUMA PASSOU".
- **Esforço humano:** 2–3 min por peça (inalterado).
- **Riscos, e eles são reais:**
  1. **Oferta escassa.** Modo A exige referência, p25 e janela ≥ 14 dias. Se a
     taxa de aprovação diária for baixa, o carrossel diário morre por falta de
     itens (o código já não publica abaixo de 2 ofertas). **Isto precisa ser
     medido antes de decidir**, com `afiliado feed --tipo termometro --dry-run`
     por alguns dias. É o maior risco de toda esta recomendação e não tenho o
     número.
  2. **Originalidade** (§1.2): o slide de foto continua sendo a peça mais
     próxima do padrão punido. Um mini-gráfico por slide resolve e é
     consistente com a orientação escrita da Meta.
  3. **Promessa nova.** "Selo" hoje significa *preço*, não *qualidade*. Se a
     peça não disser isso, a conta cria uma promessa que a régua não sustenta —
     e recria a contradição do outro lado.

### 6.2. "A prova" — a peça de uma oferta que PASSOU
- **O que é:** o gráfico de 90 dias de uma oferta **aprovada**: linha da
  mediana ("preço de sempre"), faixa do p25 ("promoção de verdade") e o ponto de
  hoje abaixo dela. Mesma mecânica de contraste do Flagrante, objeto invertido.
- **Por que atrai:** a evidência de negatividade (§4) diz que o que viaja é o
  **contraste** ("disseram X, a verdade é Y"), e o contraste sobrevive à
  inversão do sinal. A literatura de jornalismo construtivo aponta que manchete
  construtiva atrai cliques igual ou mais que a convencional
  (https://constructiveinstitute.org/research-overview-effects/) — **MÉDIA**,
  e mede emoção, atitude e intenção, **não** crescimento de seguidor no
  Instagram; não use como prova. Para o formato em si: **SEM EVIDÊNCIA**.
- **O que o pipeline já tem:** `render_grafico_preco` funciona para **qualquer**
  oferta com histórico — o Flagrante é só um seletor por cima. Os chips já
  carregam o veredito. Custo de renderização: zero.
- **Esforço humano:** 2–4 min (sem aprovação jurídica: não há acusado).
- **Risco:** sem vilão, a peça pode simplesmente não circular. Esta é **a
  aposta principal** da mudança e ela é não testada. Diga isso em voz alta.

### 6.3. "Aula do Fiscal" — educativo perene, sem produto nenhum
- **O que é:** carrossel ou Reel que ensina a mecânica: como um "de" inflado se
  parece num gráfico; o que é "preço de sempre"; por que 62% OFF sobre um preço
  que existiu um dia não é desconto; o que o Procon recomenda (desconto sobre o
  menor preço dos últimos 60 dias). **Nunca nomeia produto nem vendedor.**
- **Por que atrai:** é o registro do @doutorfran (1,3 M) e a primeira linha do
  @desrotulandoapp (895 mil) — **MÉDIA**. A demanda está medida (§5). Carrossel
  educativo é o formato de save (doc anterior, **FORTE**) e o Instagram é
  indexado pelo Google desde 10/07/2025, então cada aula vira página de busca
  de cauda longa (doc anterior, **FORTE**).
- **O que o pipeline já tem:** `render_carrossel` monta capa + slides + fecho,
  mas `_render_slide_oferta` **exige um `Post` com foto de produto**. Slide de
  texto puro não existe — é a única peça de código genuinamente nova, e é
  pequena (usa `_draw_bloco_centralizado`, que já está lá).
- **Esforço humano:** 20–30 min na primeira redação, ~2 min nas reciclagens.
- **Risco:** baixo. É o formato mais seguro em originalidade (arte 100% nossa,
  sem foto alheia), mais seguro juridicamente (sem acusado) e o único que
  **explica por que a conta existe**. O risco é não gerar clique de comissão —
  ele constrói autoridade, não venda.

### 6.4. "Vale a pena esperar?" — só com padrão medido na NOSSA série
- **O que é:** para produto cujo `price_log` mostre um padrão repetido, a peça
  diz "espere N dias". Conselho contra a comissão imediata.
- **Por que atrai:** **SEM EVIDÊNCIA** de que o formato performe. O que existe é
  a factualidade do padrão adjacente: BigDataCorp mediu inflação **antes** da
  Black Friday em 27,6 milhões de produtos (doc anterior, **FORTE**), o que
  sustenta "não compre agora" em janelas datadas. As fontes sobre "melhor mês
  para comprar" no Brasil são blogs de varejo — **ANEDÓTICA**.
- **O que o pipeline já tem:** o gráfico. Um detector de sazonalidade **não
  existe**.
- **Esforço humano:** ~5 min.
- **Risco, e é o mais insidioso da lista:** alegar ciclo com 90 dias de série é
  exatamente o tipo de afirmação sem lastro que a conta existe para punir — a
  mesma falha do "+60% com mascote" que este projeto descartou. **Só publicar
  com o número de ciclos observados impresso na peça.** Sem isso, não publicar.

### O que eu NÃO recomendo, e por quê
- **"Boletim do método"** ("conferi 120 ofertas, 14 passaram"): não viola a
  invariante, mas recentra o feed em fracasso sem entregar utilidade, e não tem
  evidência nenhuma a favor. **Vai bem em story e em destaque; não em feed.**
- **Comparação entre marketplaces na mesma peça:** preço de marketplace muda em
  horas; a peça envelhece para uma afirmação falsa, e a conta perde o único
  ativo que tem.
- **"O que ficou mais caro":** negativo, sem caminho de comissão e sem
  precedente.

---

## 7. O que fazer com o Termômetro e com o Flagrante

### Termômetro → **reenquadrar** (§6.1)
Ele funciona, a arte está pronta e o problema inteiro está na seleção e na capa.
Aposentar seria jogar fora o motor de retenção por causa de uma frase.

Há um **segundo motivo, independente da bio**, para matar a capa "5 OFERTAS. 3
PASSARAM.": a Meta define clickbait como conteúdo que retém deliberadamente
informação crucial, obrigando a pessoa a clicar para descobrir a resposta, e
lista o uso de **caixa alta** entre os sinais; a consequência é redução de
distribuição no Feed.
https://transparency.meta.com/features/approach-to-ranking/content-distribution-guidelines/clickbait-links/ ·
https://about.fb.com/news/2017/05/news-feed-fyi-new-updates-to-reduce-clickbait-headlines/
— **FORTE** quanto à definição. **Ressalva honesta:** o texto primário trata de
*manchetes de links no Feed*; aplicá-lo a uma capa de carrossel do Instagram é
**inferência minha (SEM EVIDÊNCIA)**. Mas a capa atual atende à descrição
literal — retém qual das cinco passou, em caixa alta — e trocá-la não custa nada.

### Flagrante → **manter fora do feed**, com dois destinos
Ele já não é publicado: `--tipo flagrante` despacha ao chat de operações
esperando o "ok". A recomendação é **não promovê-lo a post de feed** e dar a
ele os dois usos em que a acusação não colide com a bio:

1. **Criativo do teste pago.** `docs/crescimento.md` já escolheu o Flagrante
   como criativo do único teste de mídia do mês (Meta Ads → Telegram, R$ 150–300).
   Anúncio sai rotulado "Patrocinado", vive por distribuição paga (não por
   recomendação) e **não fica na grade** como identidade permanente da conta.
   É o lugar certo para o mecanismo mais forte de envio (§4).
2. **Versão anônima e perene.** O gráfico **já não nomeia vendedor** — ele
   imprime o **título do produto**. Suprimir o título transforma a peça em aula
   (§6.3): "este gráfico é real; o produto não importa". Perde a força do
   flagrante e ganha o direito de ficar no feed.

**E um risco novo, que empurra na mesma direção — FORTE, fonte primária:** os
Termos do Programa de Afiliados da Shopee dizem, na cláusula **2.3**, que a
Shopee pode encerrar a participação se a Mídia do Afiliado contiver "qualquer
Conteúdo Proibido **ou outro conteúdo que a Shopee considere inadequado**", e na
**7.2**, que pode "rescindir unilateralmente este Contrato a seu exclusivo
critério e por qualquer razão que ela julgar apropriada com sete (7) dias de
aviso prévio".
https://help.shopee.com.br/portal/10/article/124094-Programa-de-Afiliados-Shopee-Termos-e-Condi%C3%A7%C3%B5es
— **FORTE**

Ou seja: uma peça que acusa um anúncio da Shopee, publicada por uma conta cuja
receita é comissão da Shopee, cabe inteira dentro de uma cláusula discricionária
de 7 dias. A aposta é assimétrica: o ganho é um post viral; a perda é a receita.
Isto **não** estava no documento anterior, que só tratou do lado CONAR/#publi.

**Risco jurídico, com a precisão que faltava:** o que a peça nomeia é o
**produto/anúncio** — o que, num marketplace, identifica o vendedor na prática.
A Súmula 227 do STJ afirma que pessoa jurídica pode sofrer dano moral (honra
objetiva); a jurisprudência reconhece o direito de crítica e exige que a empresa
prove dano efetivo à imagem perante terceiros
(https://lume.ufrgs.br/bitstream/handle/10183/276818/001206403.pdf) — **MÉDIA**
(síntese de fontes secundárias; não li os acórdãos). Some-se a isso que o STJ já
responsabilizou civilmente criadores de conteúdo pelo que divulgam, mesmo com
aviso de isenção no perfil
(https://idec.org.br/idec-na-imprensa/golpistas-usam-instagram-para-aplicar-fraudes-em-vendas-line)
— **MÉDIA**: a exposição existe nos dois sentidos, ao acusar **e** ao indicar.

**Contra-argumento em boa-fé, que não quero esconder:** o @desrotulandoapp
(895 mil) **nomeia marcas e dá nota ruim a elas**, no Brasil, e cresceu fazendo
isso. A diferença que me parece decisiva é o **conflito de interesse**: ele
julga composição contra uma metodologia pública do Ministério da Saúde e **não
ganha comissão** sobre o produto que julga. Nós ganharíamos. Essa distinção é
**leitura minha (MÉDIA)**, não um achado publicado — mas é a mesma distinção que
o guia CONAR de 01/06/2026 faz ao dizer que remuneração por performance não
afasta a natureza publicitária (doc anterior, **FORTE**).

---

## 8. A contra-evidência, e o que continua sendo aposta

**O que o documento anterior estabeleceu e continua de pé:** ninguém provou que
histórico de preço ganha seguidor no Instagram brasileiro. Esta pesquisa
**reforça** isso com um caso novo: o **@reclameaqui**, a maior marca de
reputação de consumo do país, tem **277 mil** seguidores — menos de um terço do
app de rótulos. Instituição que publica veredito não vira audiência de Instagram
no Brasil.

**O que esta pesquisa NÃO prova:**

1. **Que aprovar cresce mais que acusar.** Não há medição. Estou recomendando
   trocar uma aposta não testada (acusação) por outra aposta não testada
   (aprovação). A justificativa é **coerência de marca + risco de plataforma +
   risco comercial**, não superioridade medida. Se o dono aceitar, tem de
   aceitar nesses termos.
2. **Que o registro "não seja enganado + compre com confiança" funciona sem
   audiência prévia.** Os dois casos brasileiros grandes têm o que a conta não
   tem: 5 M de downloads de app, ou um rosto com humor de esquete.
3. **Que a negatividade é dispensável.** É o único mecanismo com evidência
   **FORTE** de gerar compartilhamento (1,91×). Mudar o alvo preserva parte
   dele; quanto, ninguém mediu.
4. **Qual é a taxa de aprovação diária da régua.** Toda a recomendação 6.1
   depende dela e ela não está medida. Medir vem antes de implementar.

## 9. O que NÃO se sustenta (e por isso não entra em decisão)

Aplicando a mesma régua que descartou o "+60% de engajamento com mascote":

- **"Estudo com 2.500 criadores: 3–5 Reels/semana = +215% de engajamento e 2,7×
  mais seguidores"** (blog crescitaly, 2026): nenhuma fonte primária, nenhum
  método, nenhum autor. **DESCARTADO.**
- **"Mix ideal: 60–70% Reels, 20–30% carrossel, 10% imagem"**, **"o Instagram
  avalia seus últimos 9–12 posts para definir seu nicho"**, **"pilares
  reconfiguram o alcance em 2–3 semanas"**: blogs de ferramenta, sem n nem
  método. **ANEDÓTICA — não usar.**
- **"Conteúdo educativo tem 2,4% de engajamento"** e **"educação puxa 5,2% em
  carrossel"**: são benchmarks por **setor** (B2B, educação), não por tipo de
  conteúdo, e não transferem para um perfil de ofertas. **Não aplicável.**
- **"79% dos brasileiros pesquisam preço antes de comprar (PwC 2026)"**: só
  encontrei a citação em veículo secundário, sem link para o relatório. **Não
  rastreado — não usar.** O número usável é o do Procon-SP (§5), com n declarado.
- **Jornalismo construtivo como prova de que enquadramento positivo cresce
  conta:** a literatura existe e é séria, mas mede emoção, atitude e intenção de
  comportamento — **não** crescimento de seguidores no Instagram, e nenhum
  estudo testou conteúdo de oferta. **Não é prova. É analogia.**

---

## 10. Resumo em uma frase

A contradição não está em ter um lado acusatório — está em **acusar e vender o
mesmo objeto na mesma peça**. O precedente brasileiro que mais se parece com
este projeto (@desrotulandoapp, 895 mil) resolve isso na própria bio: acusa a
**indústria** e entrega **confiança para comprar**. Traduzido para cá: o feed
publica o que passou e ensina o truque; o flagrante vira anúncio pago e aula
anônima; e a bio passa a dizer o número que o código realmente confere.
