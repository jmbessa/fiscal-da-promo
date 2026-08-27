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
| respondeu, e o link **não** está lá (ou aponta para outro endereço) | **falha**, com o `pk` na mensagem | **+1** |
| `story_info` levantou, ou não veio `pk` | **falha**, dizendo que **não foi possível verificar** | não mexe |

O terceiro estado nunca vira "sem link": um 504 do Instagram não é prova de que
o instagrapi quebrou. E nenhum dos três vira "verificado" sem leitura.

**Desarme automático.** Ao acumular `max_sem_link` (padrão **2**) falhas de
verificação SEGUIDAS, o canal se declara indisponível pelo resto do run e
manda para o resumo de operações:

```
⚠️ instagram_story_link: 2 stories sem figurinha — canal desarmado, ligue
   instagram_story (Graph API) como fallback
```

Uma verificação bem-sucedida zera o contador. O story que saiu sem link **fica
no ar** (apagar é destrutivo, e o post em si não faz mal).

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
   `instagram_story` estiverem os dois `enabled: true`.

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
    max_per_day: 6
    max_sem_link: 2
  instagram_story:
    enabled: false           # o oficial vira fallback desligado
```

### 5. Confira

```bash
afiliado doctor
```

Ele diz se as credenciais estão **presentes** (nunca mostra o valor), a idade
da sessão, e reclama se os dois canais de story estiverem ligados. **O doctor
não faz login** de propósito: uma autenticação por diagnóstico é justamente o
que atrai desafio.

### 6. Ensaie e publique

```bash
afiliado stories --dry-run     # não publica e não escreve no banco
afiliado stories               # publica de verdade
```

## Agendar no Windows (Agendador de Tarefas)

O comando precisa rodar do diretório do projeto, com o `.env` ao lado.

1. Abra o **Agendador de Tarefas** → *Criar Tarefa* (não "tarefa básica").
2. **Geral:** marque *Executar estando o usuário conectado ou não* só se você
   souber que a rede está disponível; para IP residencial estável, o normal é
   deixar *Executar somente quando o usuário estiver conectado*.
3. **Disparadores:** *Diariamente*, repetir a cada **2 horas**, das 08:00 às
   23:00. O ritmo fino é do pipeline (`schedule:` no `config.yaml` distribui o
   `max_per_day` pela janela) — o agendador só precisa acordar o processo.
4. **Ações:** *Iniciar um programa*
   - Programa: `C:\caminho\para\.venv\Scripts\afiliado.exe`
   - Argumentos: `stories`
   - **Iniciar em:** a pasta do projeto (é de onde saem `config.yaml`, `.env` e
     `data/`).
5. **Condições:** desmarque *Iniciar a tarefa somente se o computador estiver
   ocioso*.

Variação de horário é bem-vinda: 6 stories/dia em horários irregulares parece
gente; 6 stories no minuto zero de cada hora parece robô.

## Quando o canal desarmar

O resumo de operações traz `⚠️ instagram_story_link: N stories sem figurinha —
canal desarmado`. Faça nesta ordem:

1. **Abra o story no celular** e confirme com os olhos: tem figurinha de link?
   O canal pode estar certo (o link não foi) ou pode ser mudança no formato da
   leitura.
2. **Ligue o fallback agora:** `instagram_story: enabled: true` e
   `instagram_story_link: enabled: false`. O dia volta a sair, sem figurinha,
   pela API oficial — que é o caminho autorizado e nunca desarma.
3. **Atualize o instagrapi:** `pip install -U instagrapi`. Foi um `pip install`
   que resolveu os cinco meses de 2026 — para quem estava olhando.
4. **Se o erro for de sessão** ("sessão do Instagram inválida"), rode
   `afiliado ig-login` uma vez. **Não** rode em laço, e não tente "insistir até
   entrar": desafio respondido com repetição vira bloqueio.

## Quando desligar de vez

Se aparecer `challenge_required` recorrente, `AccountSuspended`, ou qualquer
aviso do Instagram sobre atividade incomum: **desligue este canal**, ligue o
`instagram_story`, e não volte. A conta vale mais que a figurinha — a perda
medida de clique entre figurinha e "link na bio" é de 2 a 4 vezes, e a perda de
uma conta de afiliado é de 100%.
