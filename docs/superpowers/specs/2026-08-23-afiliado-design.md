# Afiliado — Pipeline automático de divulgação de ofertas com links de afiliado

**Data:** 2026-08-23
**Status:** aprovado em brainstorming; aguardando plano de implementação

## 1. Objetivo

Sistema totalmente automático que descobre ofertas na Shopee e no Mercado Livre,
gera texto de anúncio com LLM, e publica posts com o link de afiliado do dono do
projeto em canais próprios (Telegram, Instagram e, futuramente, WhatsApp),
construindo audiência do zero no formato "achadinhos" (ofertas gerais com
categorias fixas configuráveis).

Modelo de referência visual/textual dos posts: canais de promoção estilo
"TênisLinks" — foto do produto, chamada com emoji, linha `De: R$X | Por: R$Y`,
CTA e link curto de afiliado.

## 2. Decisões de contexto (fixadas nas conversas)

| Tema | Decisão |
|---|---|
| Afiliação | Usuário já cadastrado nos programas da Shopee e do Mercado Livre; hoje gera links manualmente |
| Audiência | Começa do zero em todos os canais; construir audiência faz parte do projeto |
| Nicho | "Achadinhos" geral com categorias fixas no config (ex.: casa, eletrônicos, beleza); nichar depois conforme dados de cliques |
| Autonomia | Automático para Telegram e feed do Instagram — nenhum post passa por revisão humana; a segurança vem de portões de validação no pipeline. **Stories NÃO são automáticos** (`story_dispatch`, fase 2A): o pipeline gera a arte e o link e os entrega ao chat de operações; publicar é um gesto manual do dono, e o resumo do run diz "📤 despachado p/ ops (postar no app)" (fase 5C, A12) |
| Volume | **60 ofertas/dia** somadas as duas lojas, cota 50/50 por fonte, dedupe de 30 dias (fase 5C; a conta está em `docs/superpowers/reviews/2026-08-26-descoberta-shopee.md`) |
| Sinalização de publicidade | **Nenhuma** — decisão do dono na fase 5C. O risco regulatório está registrado em `2026-08-26-analise-adversarial.md` (A7) |
| Infra | **GitHub Actions (cron) é a produção** desde a fase 5C — de hora em hora, 08:00–23:00 BRT (16 jobs/dia: o GitHub cobra cada job arredondado para o minuto seguinte, e a cadência de 30 min estourava a cota); a VPS (systemd, 5 min) fica opcional. O código não depende de nenhum dos dois |
| LLM | Cota da assinatura Claude Max via Claude Code headless (`claude -p`), token de CI gerado com `claude setup-token`; fallback para API key se `ANTHROPIC_API_KEY` estiver definida |
| Stack | Python (3.12+), SQLite para estado, pacote único com CLI |

## 3. Arquitetura escolhida

**Pipeline determinístico com IA só onde IA agrega** (abordagem A do
brainstorming). Os "3 agentes" da ideia original viram módulos de um pipeline
batch de fluxo fixo:

- descobrir → selecionar → escrever → validar → publicar → registrar;
- o LLM entra em exatamente dois pontos: **ranquear** as candidatas do run e
  **escrever a copy** de cada oferta escolhida;
- todo o resto é código determinístico e testável.

Alternativas descartadas: multi-agente autônomo (custo, não-determinismo e
dificuldade de teste sem benefício num funil fixo) e no-code/n8n (limites em
criativo, dedupe e portabilidade).

Cada execução de `afiliado run` processa um ciclo completo e termina. O
agendador externo define o ritmo — desde a fase 5C a **produção é o GitHub
Actions**: `publish.yml` de hora em hora das 08:00 às 23:00 BRT (16 jobs/dia,
`--posts-per-run 5`), commitando `data/state.db` de volta com `pull --rebase`
antes do push. A VPS (timer systemd a cada 5 min, 192 execuções/dia, 1 oferta
por run; `docs/runbooks/vps-setup.md`) fica **opcional**, para quem quiser
cadência mais fina — nunca as duas ao mesmo tempo. Cada canal tem um teto diário
(`max_per_day`: `telegram` 60 — a meta do canal —, `story_dispatch` 6,
`instagram_feed` 2), contado no **dia local** de `schedule.timezone` e, desde
a fase 5A, **distribuído pela janela** `schedule.window_start`–`window_end`:
um canal só publica enquanto o que já postou hoje está abaixo de
`min(max_per_day, floor(max_per_day × fração da janela decorrida) + 1)`;
fora da janela o orçamento é 0. Sem nenhum canal aberto o run termina antes
do ranking — nenhuma oferta paga preço ao vivo, link, copy ou validação sem
ter onde ser publicada. `story_dispatch` é **manual**: a arte e o link chegam
prontos ao chat de operações e o dono posta o story à mão — o resumo do run
diz "despachado", não "publicado".

A descoberta deixou de ser refeita a cada run (fase 5C, C1). Cada run lê uma
**fatia** do espaço da API (`shopee.calls_per_run`, cursor persistido em
`discovery_cursor`) e o resultado acumula num **estoque de candidatas** (tabela
`candidates`, validade `candidate_max_age_days`); as candidatas de um run são o
estoque ∪ a fatia da vez. Isso é o que sustenta 60/dia com dedupe de 30: a
janela de cada listagem tem 2.000 itens e as 5 raízes dão ≈ 5.460 elegíveis por
mês, contra 244 da leitura rasa anterior — medição em
`docs/superpowers/reviews/2026-08-26-descoberta-shopee.md`. Um piso de valor
esperado (`selection.min_ev_brl`, fase 1.8) segue cortando sobras.

## 4. Estrutura de componentes

```
Afiliado/
├── pyproject.toml
├── config.yaml              # categorias, desconto mínimo, faixa de preço,
│                            #   nº de posts por execução, tom da copy
├── src/afiliado/
│   ├── cli.py               # comandos: run, dry-run
│   ├── pipeline.py          # orquestra as etapas; try/except por oferta
│   ├── models.py            # dataclasses Offer e Post (normalizadas)
│   ├── sources/
│   │   ├── base.py          # interface: fetch_offers() -> list[Offer];
│   │   │                    #   resolve_affiliate_link(offer) -> str
│   │   ├── shopee.py        # Shopee Affiliate Open API (GraphQL) — fase 1
│   │   └── meli.py          # Mercado Livre — fase 3
│   ├── selection.py         # filtros por regra + ranqueamento via LLM
│   ├── copywriter.py        # LLM escreve partes criativas (JSON validado)
│   ├── message.py           # montagem do texto final (copy + preços + link)
│   ├── watchlist.py         # fase 1.6: carrega data/watchlist.json (boosts de EV e selo de menor preço)
│   ├── brand.py             # fase 2C: mascote (porte do SVG do design) usado no cabeçalho das artes
│   ├── creative.py          # fase 2C: design system Fiscal da Promo (Bricolage Grotesque + IBM Plex Mono, mascote, navy/dourado) — story 1080×1920 e feed 1080×1350
│   ├── channels/
│   │   ├── base.py          # interface: publish(post) -> PublishResult
│   │   ├── telegram.py      # Bot API — fase 1
│   │   ├── story_dispatch.py    # fase 2A: arte de story + link prontos no chat de operações (semi-auto)
│   │   ├── instagram_feed.py    # fase 2A: post de feed 100% automático via Meta Graph API
│   │   └── whatsapp.py      # fase 4
│   ├── llm.py               # wrapper `claude -p` + fallback API key
│   ├── validate.py          # portões pré-publicação
│   └── state.py             # SQLite: dedupe e histórico
├── data/state.db            # versionado no repo; Actions commita após cada run
├── data/watchlist.json      # fase 1.6: artefato semanal (análise externa); ausente/vencido não bloqueia o run
├── .github/workflows/
│   ├── publish.yml          # cron; concurrency serializa runs
│   └── tests.yml            # testes unitários em todo push
└── tests/
```

Princípios estruturais:

1. **O LLM nunca escreve preço nem link.** Preço, desconto e link de afiliado
   são injetados programaticamente no template final a partir dos dados
   estruturados da API. O LLM produz apenas as frases criativas (chamada,
   descrição, CTA, emoji).
2. **Fontes e canais são plugins.** Adicionar fonte ou canal é implementar a
   interface de `base.py`; o pipeline não muda.

## 5. Fluxo de dados de uma execução

1. **Descoberta** — cada fonte habilitada retorna `Offer`s normalizados
   (título, preço original, preço atual, % desconto, comissão, URL de imagem,
   categoria, ID). A descoberta pode consultar mais de uma ordenação da API
   (ex.: Shopee `sort_types`) e mescla os resultados com dedupe por ID. Ofertas
   de fontes distintas entram misturadas na mesma esteira a partir daqui.
2. **Filtro por regras (sem LLM)** — elimina: já postado nos últimos N dias
   (SQLite), desconto abaixo do mínimo, preço fora da faixa, categoria fora da
   lista. Sobram ~30–50 candidatas.
3. **Ranqueamento (1 chamada LLM)** — escolhe as N melhores do run (ex.: 3)
   por apelo popular e variedade de categorias (entre si e vs. posts
   recentes). Saída JSON validada; JSON inválido → fallback determinístico
   (top N por valor esperado (comissão × preço × popularidade)); a watchlist
   semanal (fase 1.6), quando válida, multiplica o valor esperado por
   categoria/item em alta.
4. **Link de afiliado** — específico por fonte (ver §6). Sem link válido, a
   oferta é descartada e a próxima do ranking assume.
5. **Copy (1 chamada LLM por oferta)** — entrada: título, categoria, desconto;
   saída JSON: chamada com emoji, linha de descrição, CTA. Comprimentos
   máximos validados.
6. **Montagem** — template junta copy + preços formatados
   (`De: R$X | Por: R$Y`) + link; imagem do produto vem da URL da API (no
   Telegram, foto com legenda); quando o preço atual está na mínima histórica
   da watchlist, injeta o selo "menor preço verificado".
7. **Validação** — portões do §8; só passa post íntegro.
8. **Publicação** — canal envia; sucesso → grava no SQLite (produto, preço,
   data, canal, message_id).
9. **Encerramento** — commit do `state.db` de volta ao repo (apenas no
   Actions, onde o runner é efêmero; na VPS o arquivo é local e persiste
   sozinho); resumo do run
   (publicados, descartados e por quê) enviado pelo mesmo bot a um chat privado
   de operações do dono. Erro grave também é notificado ali.

Custo LLM por run: ~4 chamadas curtas (1 ranking + N copies) na cota Max.

## 6. Específico por fonte

### Shopee (fase 1)
- Descoberta e geração de link 100% oficiais via Shopee Affiliate Open API
  (GraphQL): listagem de ofertas com comissão + `generateShortLink`.
- Pré-requisito: obter credenciais de API no portal de afiliados Shopee BR.

### Mercado Livre (fase 3)
- **Descoberta:** API pública de busca/ofertas do ML; exige aplicativo
  registrado no portal de desenvolvedores (gratuito) e token OAuth.
- **Link de afiliado:** não há API oficial. Spike com três estratégias, nesta
  ordem de preferência:
  1. **Parâmetros de atribuição na URL** (`matt_word`/`matt_tool` com o ID de
     afiliado): se o clique aparecer no painel de afiliado, geração de link é
     formatação de string — 100% automático.
  2. **Pool de links pré-gerados:** abastecimento semanal (~10 min) via
     linkbuilder do portal (gera em lote); pipeline consome de uma tabela de
     links prontos. Publicação continua 100% automática.
  3. **Automação de browser (Playwright)** do portal: último recurso; frágil e
     só viável na fase VPS.
- No código, a diferença fica isolada em `resolve_affiliate_link()` da fonte.

## 7. Faseamento

| Fase | Entrega | Racional |
|---|---|---|
| 1 | Shopee → Telegram, full auto, com estado, validação e chat de operações | APIs 100% oficiais dos dois lados; valida o pipeline de ponta a ponta com risco zero |
| 2 | Instagram feed + stories via Meta Graph API; criativos por template (Pillow); exige conta business/creator vinculada a página do Facebook — **estágio A entregue** (semi-auto stories via `story_dispatch` + feed via API oficial, `instagram_feed`, desligado até o runbook de setup) | Motor de crescimento de audiência |
| 3 | Mercado Livre (descoberta + spike do link, estratégias do §6) | Segunda fonte de ofertas |
| 4 | WhatsApp via biblioteca não-oficial, rodando na VPS | Só quando houver audiência que justifique o risco de banimento do número; risco documentado e aceito explicitamente na hora |

Cada fase é um ciclo próprio de plano → implementação → validação.

## 8. Portões de validação (pré-publicação)

- **Link (offline, fase 5A):** `https`, host igual a um dos
  `validation.allowed_domains` ou subdomínio dele, sem espaço nem caractere
  de controle. **Nenhuma requisição HTTP ao link de afiliado** — um GET do
  próprio pipeline no link curto (IP da VPS, User-Agent falso, segundos após
  a geração, em todo post e no dry-run) é um clique de afiliado artificial:
  assinatura de tráfego inválido para os programas e contaminação do teste de
  atribuição. A vitalidade da oferta é provada pelo `refresh_price` (segundos
  antes de publicar — e desde a fase 5C a descoberta pode ter dias, porque a
  candidata vem do estoque: item que saiu da listagem levanta `SourceError` e
  é descartado ali); o link vem do gerador oficial/painel. Inválido →
  descarta a oferta.
- **Preço:** dentro da faixa do config e não acima da referência própria
  (`selection.max_above_ref`, ver fase 4); roda DEPOIS do `refresh_price`.
  O desconto do vendedor não é mais critério — é rótulo, e quem o decide é
  `pricing.verdict` (fase 5B), uma vez, para texto, arte, legendas e copy.
- **Imagem:** URL responde, content-type de imagem, tamanho mínimo. É a única
  checagem que vai à rede; o `--dry-run` a pula.
- **Copy:** JSON validado contra schema (campos obrigatórios, comprimentos
  máximos, sem URL dentro do texto). Inválido → 1 retry; falhou → copy de
  template padrão sem LLM. Nunca publica post malformado.

## 9. Política de falhas

Regra geral: uma oferta ruim não derruba o run; uma fonte ruim não derruba as
outras; o run só aborta se todas as fontes falharem. Nada falha em silêncio —
tudo aparece no resumo de operações (cada aviso uma vez por dia local, para o
chat de ops não virar ruído), exceto o run vazio sem aviso novo (fase 1.8),
que é ausência de evento, não falha; o caminho de exceção (run abortado ou
interrompido) sempre notifica, e o primeiro run do dia sempre manda um
heartbeat.

| Falha | Comportamento (como implementado na fase 5A) |
|---|---|
| API de fonte fora | Shopee: 1 tentativa + até 3 repetições com backoff (0,5 s, 1,5 s, 4 s) em 429, 5xx e erro de conexão; persistindo, a fonte falha, vira aviso ("fonte shopee falhou: …") e o run segue com as outras. Só quando TODAS as fontes falham o run aborta — e ainda assim o resumo com os avisos vai ao ops |
| Fonte habilitada devolve 0 ofertas | Aviso "`<fonte>`: 0 ofertas buscadas" (o ML acrescenta o motivo do pool) |
| Filtro zera N > 0 ofertas | Aviso "N ofertas buscadas, 0 candidatas — dedupe: a · faixa de preço: b · acima da referência: c · sem dados: d · categoria: e · EV: f" |
| Oferta falha em qualquer etapa | Descarta, promove a próxima do ranking, segue. No resumo, descartes com o mesmo motivo (≥ 4) viram uma linha: "31× preço acima da referência (ex.: …)" |
| Nenhuma oferta atinge o piso de EV (`min_ev_brl`, fase 1.8) | Candidatas abaixo do piso são cortadas no filtro (contam em "EV"); se sobrar 0, o run publica nada, sem erro |
| Run sem nada a contar — nada publicado, nada descartado, nenhum aviso novo (fase 1.8, cadência de 5 min) | Resumo de operações NÃO é enviado (evita 192 mensagens/dia); opcional `ops.notify_empty_runs: true` restaura o envio sempre. O heartbeat do primeiro run do dia ("☀️ Bom dia — ontem: N publicados, M descartados em K runs") é um aviso e sempre chega |
| Aviso de estado persistente (watchlist vencida, pool vencido, teto, canal sem env, LLM caído) | Entra no resumo uma vez por dia local (tabela `warned`, chave = texto sem dígitos); em dry-run nada é gravado |
| LLM indisponível no ranking | Fallback determinístico: top N por valor esperado (EV); `{"chosen": null}` ou qualquer coisa que não seja lista também cai no fallback, sem exceção |
| LLM indisponível na copy | Copy de template padrão. Ao fim do run, "LLM indisponível em X de Y chamadas — ranking/copy de fallback" |
| Publicação falha | Telegram: 3 tentativas em erro de rede; `429` com `retry_after` ≤ 30 s dorme e repete uma vez (sem cair para `sendMessage`), acima disso falha. Falhou → não grava como publicado (volta candidato no próximo run). Contagem de `posts_per_run` é **por oferta**, não por canal: uma oferta conta como publicada se ao menos um canal aceitar; canais com `max_per_run` (ex.: `instagram_feed`, limite 1) pulam a oferta sem contar como falha quando o limite do run já foi atingido |
| Teto diário por canal (`max_per_day`) | Contado no SQLite no **dia local** e distribuído pela janela (`schedule:`, ver §3). Canal fechado pelo ritmo é pulado em silêncio; canal no teto de verdade vira aviso. Nenhum canal aberto (antes do ranking ou após uma publicação) → o run termina sem pagar LLM/link/validação pelas ofertas restantes |
| Resumo de ops gigante | `send_text` divide em mensagens de até 4000 caracteres em quebras de linha; `ok: false` da API vai ao journal com a `description` |
| Run interrompido por sinal (`TimeoutStartSec`, SIGINT) | O CLI avisa "❌ Run interrompido (sinal n)" e sai com 128+n |
| Unidade systemd morre (OOM, venv quebrado) | `OnFailure=afiliado-notify.service` → "❌ unidade afiliado falhou — ver journalctl -u afiliado" |
| Runs simultâneos | Impossível: `concurrency` no workflow serializa; na VPS, oneshot ativo não é sobreposto pelo timer |

## 10. Testes

- **Unitários com fixtures gravadas:** respostas reais das APIs salvas em JSON;
  cada módulo testado sem rede, incluindo casos de erro (preço zerado, JSON
  inválido do LLM, link 404).
- **Golden tests** da mensagem final montada (diff caractere a caractere).
- **`afiliado dry-run`:** pipeline completo com APIs e LLM reais, imprimindo os
  posts no terminal em vez de publicar — validação manual antes de qualquer
  mudança entrar no canal.
- **CI:** testes unitários em todo push (`tests.yml`), separado do workflow de
  publicação.

## 11. Segredos e configuração

- GitHub Secrets (e `.env` na VPS): credenciais Shopee Affiliate API, token do
  bot Telegram, ID do canal, ID do chat de operações, token do
  `claude setup-token`; nas fases seguintes, token Meta Graph e credenciais do
  app ML.
- `config.yaml` versionado: categorias, desconto mínimo, faixa de preço, nº de
  posts por execução, tom da copy, fontes/canais habilitados; seção opcional
  `watchlist.path` (fase 1.6) aponta para o artefato semanal de boosts/selo.

## 12. Fora de escopo (por ora)

- Encurtador/domínio próprio de links (ex.: estilo tenislinks.com) — usar os
  links curtos das próprias plataformas.
- Métricas de clique próprias — usar os painéis de afiliado das plataformas.
- Vídeo/Reels e geração de imagem por IA — criativos são template-based.
- Dashboard web — operação via chat de operações no Telegram e `config.yaml`.
