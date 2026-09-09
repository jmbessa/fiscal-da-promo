# O X (Twitter) — como ligar, e o que ele custa

O canal `x` publica **texto puro, sem nenhuma URL**, e manda quem lê para o
Telegram pelo **link da bio**. Este runbook tem o passo a passo para o dono
criar o app, e a conta que justifica esse desenho.

## Por que sem link — os dois números

**Dinheiro.** Em **06/02/2026** o X trocou os planos por *pay-per-use* e acabou
com o tier grátis para desenvolvedor novo. Em **20/04/2026** o preço de um post
COM link subiu de US$ 0,01 para **US$ 0,20**; um post sem link custa
**US$ 0,015**. Treze vezes mais caro.

**Alcance.** O X reduz a distribuição de post com link para segurar quem lê na
plataforma. As medições de 2026 divergem no tamanho (de 30–50% a menos de
alcance inicial até 94% em um teste de Q1), mas concordam na direção — e para
conta **sem Premium** o engajamento mediano de post com link fica perto de
zero.

Somadas, elas dizem a mesma coisa: **com link a gente paga 13× para alcançar
menos.**

| modelo | 60 posts/dia | 10 posts/dia |
|---|---:|---:|
| texto puro (o nosso) | ~US$ 27/mês | ~US$ 4,50/mês |
| com link no post | ~US$ 360/mês | ~US$ 60/mês |

E há um terceiro ganho, que não é de custo: **sem link de afiliado no X, não
importa se ele está ou não na lista de canais declarados no painel da Shopee.**
A pesquisa de 2026-09-02 não confirmou que esteja, e publicar link de afiliado
em canal não declarado custa a monetização inteira, não uma multa.

## O que o post parece

```
Fone de Ouvido Bluetooth TWS
R$ 89,90
⭐ 4,8 · 1 mil vendidos

Quem conferiu? O Fiscal.
Todas as ofertas com link no meu Telegram — está na bio.
```

O título é a única parte elástica: ele encolhe para o resto caber nos 280
caracteres. O que sustenta a marca — o preço e a assinatura — nunca é o que
some.

## Passo a passo (o dono faz; o Claude não cria conta nem digita senha)

1. **Portal do desenvolvedor.** Entre em `developer.x.com` com a conta
   `@ofiscaldapromo` e crie um **Project** e um **App** dentro dele.
2. **Billing.** O pay-per-use cobra por uso e exige cartão. Sem isso o app
   existe e não publica.
3. **Permissão do app.** Em *User authentication settings*, ponha o app em
   **Read and write**. O padrão é só leitura, e com ele o `POST /2/tweets`
   volta 403 — é o erro mais comum aqui.
4. **Chaves.** Na aba *Keys and tokens*, gere:
   - **API Key** e **API Key Secret** (do app);
   - **Access Token** e **Access Token Secret** (do usuário).

   Se você mudar a permissão do app DEPOIS de gerar o Access Token, **gere o
   token de novo** — o antigo guarda a permissão antiga.
5. **`.env`** (as quatro linhas, na pasta do projeto — o Claude nunca lê nem
   pede o conteúdo delas):

   ```
   X_API_KEY=...
   X_API_SECRET=...
   X_ACCESS_TOKEN=...
   X_ACCESS_TOKEN_SECRET=...
   ```
6. **A bio.** Ponha `t.me/fiscaldapromo` no campo de site do perfil. É o único
   destino que o canal tem — o post nunca traz endereço.
7. **Ligar**, em `config.yaml`:

   ```yaml
   channels:
     x:
       enabled: true
       max_per_day: 10
   ```
8. **Conferir:** `afiliado doctor`. O item `x` diz se a credencial está lá e
   **quanto o teto custa por mês**.

## Como o canal se comporta

- **Uma oferta por post**, uma por run (`max_per_run: 1`), com o mesmo ritmo e
  o mesmo teto diário dos outros canais.
- **O portão da URL é a última linha de defesa** e recusa o post *antes* de
  qualquer chamada. O título do vendedor, que é dado de terceiro, é **saneado**
  antes disso: "Kit 3 Cremes t.me da marca X" vira "Kit 3 Cremes da marca X" e
  a oferta continua publicável.
- **Erro do X marca `publicado=True`.** O pedido foi feito e a resposta não
  prova que nada saiu; repetir aqui custa dinheiro por post. É a mesma regra da
  fase 5X, e aqui ela é ainda mais clara.

## Limites da API que valem saber

- **10.000 posts por 24 h** por app e **100 por 15 min** por usuário. O nosso
  teto está uma ordem de grandeza abaixo.
- Não há mínimo mensal: se o canal ficar desligado um mês, o custo é zero.

## O que NÃO fazer

**Publicar pelo navegador para não pagar a API.** É a mesma classe do
`instagrapi` que este projeto desligou em 2026-08-30: violação de termos, na
conta que sustenta a operação. O canal existe pela API ou não existe.

## O que ainda não está aqui

**As threads dos temas.** Os 14 carrosséis editoriais de `data/temas.yaml`
viram thread naturalmente — cada slide é um post, e thread não tem link nenhum,
então custa US$ 0,015 por post e não sofre penalidade. É o formato que o X mais
distribui. Não foi construído ainda porque o canal de oferta vem primeiro: ele
é o que prova que a credencial, a assinatura OAuth e o ritmo funcionam.
