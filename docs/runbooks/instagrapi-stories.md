# Runbook — story com figurinha de link pelo instagrapi (fase 5F)

Como ligar, operar e desligar o canal `instagram_story_link`: o story que sai
com **figurinha de link clicável** para o produto.

> **O risco, em uma frase honesta:** o instagrapi se faz passar pelo aplicativo
> móvel do Instagram, exige **usuário e senha** (não é token revogável), o
> próprio mantenedor diz que ele serve para "testing, research and controlled
> internal automation" — e **não existe taxa de ban publicada e confiável** para
> contas que só publicam. A moeda de risco é a conta `@ofiscaldapromo`.

## Por que este canal existe

A Graph API **nega** figurinhas, com todas as letras, na referência do mesmo
endpoint que o canal `instagram_story` usa:

> "Publishing stickers (i.e., link, poll, location) is not supported; however
> mentioning users without a sticker is supported."

O Meta Business Suite oferece figurinha de link em story agendado — mas o dono
verificou no compositor real, em 2026-08-27, e o aviso lá é
*"os links serão mostrados apenas nos stories do Facebook"*. **Não vale para o
Instagram.** Nenhum agendador do mercado (Buffer, Later, Metricool, Sprout,
mLabs) automatiza o sticker: todos caem em "publicação por notificação", que
exige um toque humano.

Ou seja: **não existe caminho de primeira parte.** O levantamento completo, com
fontes e datas, está em
[`docs/superpowers/reviews/2026-08-27-sticker-de-link.md`](../superpowers/reviews/2026-08-27-sticker-de-link.md).

## O que o canal faz de diferente de qualquer exemplo de instagrapi

Ele **não acredita no retorno do upload**. O sticker de link do instagrapi
ficou quebrado **em silêncio** de **2025-11-03 a 2026-04-16** (issue #2320): o
story ia ao ar, o upload dizia "publiquei" e o link não estava lá. Para um
pipeline de afiliados esse é o pior modo de falha possível — gasta cota, gasta
a atenção do seguidor e não converte nada.

Por isso, depois de publicar, o canal lê o story de volta com `story_info(pk)`
e distingue **três** estados:

| O que ele viu | Resultado | O que acontece com o contador |
|---|---|---|
| `story.links` traz o `webUri` pedido | publicação de verdade | zera |
| respondeu, e o link **não** está lá | **falha**, com o `pk` na mensagem | **+1** |
| `story_info` levantou, ou não veio `pk` | **falha**, dizendo que **não foi possível verificar** | não mexe |

O terceiro estado nunca vira "sem link": um 504 do Instagram não é prova de que
o instagrapi quebrou. E nenhum dos três vira "verificado" sem leitura.

A linha do meio vem com **qual** ausência foi — e a diferença importa na hora
de decidir o que fazer:

| Na mensagem | O que provavelmente aconteceu | O que fazer |
|---|---|---|
| `o story respondeu com \`links\` vazio` | o instagrapi quebrou de novo (é o modo de falha de 2025-11 a 2026-04) | `pip install -U instagrapi`; ligue o fallback |
| `não encontrei o campo \`links\` na resposta do story_info` | a **leitura** mudou de formato — o story pode estar perfeito | **abra o story no celular antes de qualquer coisa** |
| `os itens de \`links\` não trazem \`webUri\` que eu saiba ler` | idem: o campo existe, o formato não é o esperado | idem |
| `a figurinha aponta para outro endereço` | o link foi, mas errado | confira o link de afiliado da oferta |

Isso existe porque o contrato `links[*].webUri` veio da **documentação** do
instagrapi, não de observação nossa. Se a biblioteca renomear o campo um dia, a
mensagem certa é "mudou o formato" — e não "o link não foi", que faria você
desligar um canal saudável.

**Desarme automático — e ele dura o DIA, não o run.** Ao acumular
`max_sem_link` (padrão **2**) falhas de verificação SEGUIDAS, ou ao encontrar
**um** desafio/sessão morta, o canal se declara indisponível e manda para o
resumo de operações:

```
⚠️ instagram_story_link: 2 stories sem figurinha — canal desarmado, ligue
   instagram_story (Graph API) como fallback
```

O desarme é **gravado no banco local** (`data/state_stories.db`, marca do dia).
O agendador vai acordar o processo de novo daqui a duas horas: sem isso, o
processo novo começaria armado e voltaria a publicar stories sem link — foi
medido, com o pipeline real: **12 stories quebrados por dia**, indefinidamente,
sem que o teto diário visse nenhum deles. Com a marca, o dia inteiro não
passa de `max_sem_link` tentativas.

**Como rearmar**, do mais barato ao mais definitivo:

1. **Espere a virada do dia local.** A marca vale por um dia e some sozinha —
   se a causa era passageira, amanhã o canal já acorda armado.
2. **`afiliado ig-login`**, quando a mensagem falou em sessão. Um login
   bem-sucedido apaga a marca na hora: é a prova de que a sessão voltou. Rode
   **uma vez** — desafio respondido com repetição vira bloqueio.
3. **Uma verificação boa** zera o contador de "sem figurinha" sozinha, no
   primeiro story que sair com link.

E note o que o desarme **não** faz: o story que saiu sem link **fica no ar**
(apagar é destrutivo, e o post em si não faz mal). Ele conta para o teto
diário e para o dedupe, porque está na conta e o público o viu — o que ele não
faz é converter.

## As duas regras que não se negociam

1. **Nunca rode este canal no GitHub Actions.** O IP de lá é de datacenter e
   **muda a cada execução**; sessão de app móvel forjada + IP novo a cada hora é
   o padrão que mais dispara `challenge_required`. É por isso que ele tem
   comando próprio (`afiliado stories`) e que o `afiliado run` — o comando que o
   Actions executa — **ignora este canal mesmo ligado**, com aviso. Rodar o
   instagrapi do Actions é o caminho mais rápido para perder a conta.
2. **Nunca ligue os dois canais de story ao mesmo tempo.** Publicar pela API
   privada e pela oficial na mesma conta, no mesmo dia, é o padrão que chama
   atenção. O `doctor` reclama (❌) se `instagram_story_link` e
   `instagram_story` estiverem os dois `enabled: true` — e o `afiliado stories`
   **recusa montar** o canal de figurinha nesse estado, com o aviso apontando
   para o doctor. Falha fechada: quem não sobe é o canal de risco.

## O que o `afiliado stories` monta — e o que ele não monta

Ele monta **um** canal: o `instagram_story_link`. Só ele.

- O `instagram_story` (Graph API) e o `story_dispatch` saem pelo `afiliado
  run` — outra tarefa do Agendador (`FiscalDaPromo-Run`), e o que o fallback
  do Actions executa. Se este comando os montasse também, seriam **dois tetos
  diários e dois dedupes** sobre a mesma conta — o comando avisa e os ignora.
- Ele usa um **banco de estado próprio**: `state.stories_path` no
  `config.yaml`, padrão `data/state_stories.db`, no `.gitignore`. O
  `data/state.db` é rastreado no git e o Actions o commita a cada run; se o
  comando local escrevesse nele, todo `git pull` viraria conflito binário.

**O preço disso, para você saber:** o dedupe e o histórico de preços deste
canal ficam **independentes** do resto. Um produto que saiu no Telegram de
manhã pode virar story à tarde — são superfícies diferentes, mas é bom não se
assustar. E o estoque de candidatas e o `price_log` desse
banco são locais: a descoberta roda da **sua** máquina, com as credenciais
Shopee/ML do seu `.env`, e não aproveita nada do que o Actions descobriu.

## Ligar, passo a passo

### 1. Instale o extra

```bash
pip install -e .[stories]
```

O `instagrapi` **não** é dependência obrigatória: o import é preguiçoso e o
resto do pipeline (e a suíte de testes) roda sem ele.

### 2. Ponha as credenciais no `.env`

```
IG_USERNAME=ofiscaldapromo
IG_PASSWORD=...        # a SENHA da conta. Não é token revogável.
IG_TOTP_SEED=          # só se a conta tem 2FA por app autenticador
```

- Elas moram **só na máquina do dono**. Não coloque nos GitHub Secrets, não
  coloque no `.env` da VPS.
- **2FA:** o instagrapi só faz **TOTP** (Google Authenticator e similares).
  **SMS não funciona.** Se a conta está com 2FA por SMS, migre para app
  autenticador antes — e guarde a semente em `IG_TOTP_SEED`.

### 3. Crie a sessão

```bash
afiliado ig-login
```

Ele lê `IG_USERNAME`/`IG_PASSWORD` do ambiente (nunca da linha de comando, que
fica no histórico do shell e no `ps`), faz **um** login e grava
`data/ig_session.json` — cookies e perfil de device. Esse arquivo é o que evita
"device novo a cada login", que é o que dispara desafio. Ele está no
`.gitignore`: **é credencial viva, nunca commite**.

Saída esperada:

```
✅ ig-login: sessão salva em data/ig_session.json
```

### 4. Ligue o canal (e desligue o oficial)

Em `config.yaml`:

```yaml
channels:
  instagram_story_link:
    enabled: true            # o de figurinha
    max_per_day: 60          # decisão do dono (2026-08-27); ~1 a cada 15 min
    max_sem_link: 2
  instagram_story:
    enabled: false           # o oficial vira fallback desligado
```

> **O número 60 é o primeiro a baixar** se aparecer `challenge_required`: este
> canal é API privada, e volume é justamente o que atrai verificação de
> segurança. O ativo em risco é a conta.

### 5. Confira

```bash
afiliado doctor
```

Ele diz se as credenciais estão **presentes** (nunca mostra o valor), **há
quantos dias a sessão foi gravada pela última vez**, e reclama se os dois
canais de story estiverem ligados. **O doctor não faz login** de propósito: uma
autenticação por diagnóstico é justamente o que atrai desafio.

> O número da sessão é a última **gravação**, não a idade do device: o canal
> reescreve o arquivo a cada login bem-sucedido. Sessão antiga aí é boa
> notícia — quer dizer que ninguém precisou logar de novo.

### 6. Ensaie e publique

```bash
afiliado stories --dry-run     # não publica e não escreve no banco
afiliado stories               # publica de verdade
```

## Agendar no Windows

**Não faça isto à mão.** Desde a fase 5I existe um script que cria a tarefa (e
as outras três da produção), idempotente e com `-Remover`:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\agendar-windows.ps1
afiliado doctor      # ele confere se as tarefas existem e estão habilitadas
```

O procedimento completo — cadência, ordem da virada, como conferir que rodou e
como voltar para o Actions — está em
[`docs/runbooks/producao-windows.md`](producao-windows.md). O que vale saber
aqui:

- a tarefa é `FiscalDaPromo-Stories`, roda `afiliado stories --posts 4` **a
  cada 15 minutos** das 08:08 às 23:15, e **inicia na pasta do projeto** (é de
  onde saem `config.yaml`, `.env` e `data/`);
- 15 min, e não as 2 h que este runbook pedia até a 5F: com
  `max_per_day: 60`, 2 h entregariam 8 stories/dia — ou rajadas de 7 pelo
  ritmo, que é justamente o que `pacing_budget` existe para evitar. Um story a
  cada ~15 min é o que o `config.yaml` já dizia;
- o minuto de início é **irregular** de propósito: 60 stories no minuto zero de
  cada hora parecem robô;
- a tarefa roda como o usuário **interativo** — ela só dispara com você
  conectado, e é assim que o Agendador aceita a tarefa **sem guardar senha**.

## Quando o canal desarmar

O resumo de operações traz `⚠️ instagram_story_link: N stories sem figurinha —
canal desarmado`. Enquanto você não fizer nada, o canal **fica calado até a
virada do dia** — e o aviso reaparece uma vez por dia, sem repetir a cada run.
Faça nesta ordem:

1. **Abra o story no celular** e confirme com os olhos: tem figurinha de link?
   O canal pode estar certo (o link não foi) ou pode ser mudança no formato da
   leitura — a tabela de mensagens acima diz qual dos dois é mais provável.
2. **Ligue o fallback agora:** `instagram_story: enabled: true` e
   `instagram_story_link: enabled: false`. O dia volta a sair, sem figurinha,
   pela API oficial — que é o caminho autorizado e nunca desarma. Ligue os dois
   ao mesmo tempo é o que **não** se faz: nesse estado o `afiliado stories`
   recusa montar o canal de figurinha.
3. **Atualize o instagrapi:** `pip install -U instagrapi`. Foi um `pip install`
   que resolveu os cinco meses de 2026 — para quem estava olhando.
4. **Se o erro for de sessão** ("sessão do Instagram inválida — rode `afiliado
   ig-login`"), rode `afiliado ig-login` **uma vez**. Ele renova a sessão e
   rearma o canal na mesma tacada. **Não** rode em laço, e não tente "insistir
   até entrar": desafio respondido com repetição vira bloqueio. Essa mensagem
   aparece agora também quando o desafio chega no **upload** ou na leitura de
   volta — que é o caso comum, porque com a sessão salva o login costuma passar
   direto.

## Quando desligar de vez

Se aparecer `challenge_required` recorrente, `AccountSuspended`, ou qualquer
aviso do Instagram sobre atividade incomum: **desligue este canal**, ligue o
`instagram_story`, e não volte. A conta vale mais que a figurinha — a perda
medida de clique entre figurinha e "link na bio" é de 2 a 4 vezes, e a perda de
uma conta de afiliado é de 100%.
