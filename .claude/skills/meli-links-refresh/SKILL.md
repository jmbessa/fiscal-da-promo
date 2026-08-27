---
name: meli-links-refresh
description: Gera links de afiliado em lote para os itens do pool curado do Mercado Livre (data/meli_offers.json) que ainda não têm link em data/meli_links.json, usando o painel interno (sessão por cookies). Rodar quando o pool for atualizado ou o doctor avisar "pool vazio ou vencido". Requer cookies de uma sessão logada em mercadolivre.com.br — via ferramentas do Chrome ou colados manualmente do DevTools.
---

# Geração de links de afiliado do Mercado Livre em lote

Preenche `data/meli_links.json` (lido por `MeliSource.resolve_affiliate_link`)
com links curtos gerados pelo painel de afiliados do Mercado Livre, para os
itens do pool curado (`data/meli_offers.json`) que ainda não têm link. Não
existe API pública para isso — o endpoint usado é interno do painel
(`/afiliados/linkbuilder`), autenticado por sessão via cookies do navegador,
não por OAuth. A função que faz as chamadas já existe em
`src/afiliado/meli_links.py` (`gerar_links`); este skill descreve o
procedimento para usá-la.

**Os cookies da sessão nunca são commitados nem gravados em arquivo — ficam
só na memória desta sessão do Claude Code**, usados apenas para autenticar as
chamadas HTTP feitas agora. Só `data/meli_links.json` (links públicos, sem
segredo) é gravado em disco.

**Estado em 2026-08-26: `data/meli_links.json` NÃO EXISTE em nenhum checkout e
nunca foi commitado.** Ele não vem com o repositório — é ESTE skill que o
gera, e depois de gerado ele PRECISA ser commitado (`git add
data/meli_links.json`), senão a VPS e o Actions continuam sem link e cada
oferta do Mercado Livre vira um descarte. Enquanto a cobertura for zero e
`sources.meli: true`, o `afiliado doctor` falha com ❌ apontando para cá
(fase 5C, A6).

## Pré-requisitos

- Executar da raiz do repo.
- Uma conta do Mercado Livre com a **tag de afiliado já cadastrada** no
  painel (`config.yaml` → `meli.tag`, ex. `jmbessa`) — tag inexistente faz o
  item falhar (`total_error`) sem quebrar o lote inteiro.
- Uma sessão logada em `mercadolivre.com.br` — ver Passo 2.

## Passo 1 — Quem precisa de link

1. Leia `data/meli_offers.json` (curadoria atual) e `data/meli_links.json`
   (pool de links já existente; se o arquivo não existir, trate como `{}`).
2. Liste os `product_id` que estão no pool curado mas **não** têm entrada em
   `data/meli_links.json`. Se a lista estiver vazia, informe ao usuário e
   pare — nada a fazer.

## Passo 2 — Obter a sessão (cookies + CSRF token)

Duas opções, tente a primeira:

**A) Ferramentas do Chrome** (`mcp__claude-in-chrome__*`; carregue via
ToolSearch se estiverem deferidas):
1. Confirme com o usuário que há uma aba logada em `mercadolivre.com.br`
   (peça para abrir e logar se não houver).
2. Navegue até `https://www.mercadolivre.com.br/afiliados/linkbuilder`.
3. Gere um link manualmente pela UI (ou deixe o usuário gerar um) para
   provocar uma chamada real a `createLink`.
4. Use `read_network_requests` para capturar essa chamada e ler os headers
   `cookie` e `x-csrf-token` que o próprio navegador enviou.
5. Se o cabeçalho `cookie` não vier completo (alguns cookies são
   `HttpOnly` e não aparecem via JS, mas devem aparecer nos headers reais
   da requisição de rede), prossiga para a opção B.

**B) Colar manualmente** (sempre funciona, peça ao usuário):
1. Peça para abrir `https://www.mercadolivre.com.br/afiliados/linkbuilder`
   logado, abrir o DevTools → aba **Network**, gerar um link qualquer pela
   UI, e localizar a requisição para `createLink`.
2. Peça para copiar e colar aqui o cabeçalho **`cookie`** completo e o
   **`x-csrf-token`** dessa requisição (aba Headers → Request Headers).

Nunca ecoe os cookies de volta na conversa além do necessário para o passo
seguinte, e nunca os escreva em arquivo.

## Passo 3 — Chamar `gerar_links`

Escreva um script Python temporário com a ferramenta **Write** (não heredoc
de shell — cookies/CSRF têm caracteres que heredoc bash pode corromper) e
rode com `PYTHONPATH="$PWD/src" python <script>`:

```python
import json
from pathlib import Path

from afiliado import config
from afiliado.meli_links import gerar_links

cfg = config.load_config("config.yaml")
me = cfg["meli"]

faltantes = [...]  # product_ids do Passo 1

links, erro = gerar_links(
    faltantes,
    tag=me.get("tag", "jmbessa"),
    cookies="<COOKIE_HEADER_DO_PASSO_2>",
    csrf="<X_CSRF_TOKEN_DO_PASSO_2>",
)

if erro:
    print(f"ERRO: {erro}")
else:
    pool_path = Path(me.get("links_path", "data/meli_links.json"))
    atual = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.is_file() else {}
    atual.update(links)  # nunca sobrescreve com valor vazio; só adiciona/atualiza os que vieram
    pool_path.write_text(json.dumps(atual, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(links)} gerados de {len(faltantes)} pedidos")
```

Se `erro` vier preenchido (sessão expirada/inválida — HTTP 401/403 ou todos
os lotes com `total_success == 0`), pare aqui e mostre a mensagem ao
usuário; volte ao Passo 2 para recoletar cookies frescos antes de tentar de
novo.

## Passo 4 — Mesclar, mostrar e commitar

1. O merge acima em `data/meli_links.json` já preserva os links existentes —
   `gerar_links` só devolve os que deram certo, então itens que falharam
   simplesmente continuam ausentes (ficam para a próxima rodada).
2. Mostre ao usuário: quantos foram gerados, quantos ainda faltam (itens que
   pediu e não vieram na resposta — provavelmente `tag` incorreta ou item
   sem afiliação disponível).
3. Commit: `chore: gera links de afiliado do ML (N novos)` terminando com
   `Co-Authored-By:` do modelo em uso. Se o repo tiver remote, pergunte
   antes de dar push.

## Notas

- Item do pool sem link em `data/meli_links.json` não quebra o pipeline: é
  descartado silenciosamente (`resolve_affiliate_link` levanta `SourceError`,
  a próxima oferta da fila assume) — rodar este skill regularmente evita que
  isso vire a norma.
- `gerar_links` é idempotente por (produto, tag): rodar de novo para um item
  que já tem link não cria duplicata nem muda o link — pode rodar com
  segurança sempre que o pool curado for atualizado.
- Tag inexistente no painel faz `total_error` subir para os itens dela, sem
  derrubar o lote inteiro — confira `meli.tag` em `config.yaml` contra o
  painel de afiliados se muitos itens falharem de uma vez.
