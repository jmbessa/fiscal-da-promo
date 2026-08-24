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
| Autonomia | Totalmente automático — nenhum post passa por revisão humana; a segurança vem de portões de validação no pipeline |
| Infra | GitHub Actions (cron) no início; design portátil para VPS (mesmo CLI via cron/systemd, sem dependência do Actions no código) |
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
agendador externo (cron do Actions; depois cron/systemd na VPS) define o
ritmo — fase 1.7: de hora em hora, 08h–23h BRT (16 execuções/dia, 3 ofertas
por run). Canais com esforço manual ou limites de audiência/API (ex.:
`story_dispatch`, `instagram_feed`) ganham um teto diário opcional
(`max_per_day`) para não saturar mesmo com o ritmo horário.

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

- **Link:** HTTP 200 e redireciona para o domínio esperado
  (shopee.com.br / mercadolivre.com.br). Morto → descarta a oferta.
- **Preço:** `atual < original`; desconto anunciado ≈ calculado (tolerância
  1%); dentro da faixa do config. Pega preço zerado/trocado da API.
- **Imagem:** URL responde, content-type de imagem, tamanho mínimo.
- **Copy:** JSON validado contra schema (campos obrigatórios, comprimentos
  máximos, sem URL dentro do texto). Inválido → 1 retry; falhou → copy de
  template padrão sem LLM. Nunca publica post malformado.

## 9. Política de falhas

Regra geral: uma oferta ruim não derruba o run; o run só aborta se não houver o
que publicar. Nada falha em silêncio — tudo aparece no resumo de operações.

| Falha | Comportamento |
|---|---|
| API de fonte fora | Retry com backoff (3x); persistindo, aborta o run e notifica operações |
| Oferta falha em qualquer etapa | Descarta, promove a próxima do ranking, segue |
| LLM indisponível no ranking | Fallback determinístico: top N por valor esperado (EV) |
| LLM indisponível na copy | Copy de template padrão |
| Publicação falha | Retry 3x; falhou → não grava como publicado (volta candidato no próximo run). Contagem de `posts_per_run` é **por oferta**, não por canal: uma oferta conta como publicada se ao menos um canal aceitar; canais com `max_per_run` (ex.: `instagram_feed`, limite 1) pulam a oferta sem contar como falha quando o limite do run já foi atingido |
| Teto diário por canal (`max_per_day`, fase 1.7) | Pula o canal sem contar como falha quando o teto (contado no SQLite, dia UTC) já foi atingido; oferta segue publicando nos demais canais |
| Runs simultâneos | Impossível: `concurrency` no workflow serializa |

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
