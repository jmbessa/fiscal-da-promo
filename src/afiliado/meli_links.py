"""Geração de links de afiliado em lote pelo painel do Mercado Livre, e o
formato do pool de links (`data/meli_links.json`).

Não existe API pública para isso — o endpoint usado aqui é interno do
painel (`/afiliados/linkbuilder`), autenticado por sessão via cookies do
navegador (não por OAuth/Bearer, diferente do resto de `sources/meli.py`).
Testado contra o endpoint real: aceita lote (múltiplos itens numa chamada),
é idempotente por (URL, etiqueta) — repetir devolve o mesmo link curto do
painel — e a etiqueta (`tag`) precisa existir previamente no painel de
afiliados, senão o item falha (`total_error`).

**Fase 5M: o link é do ANÚNCIO, não da página de catálogo.** O preço que o
post publica é o de um anúncio específico (`refresh_price` escolhe o mais
barato entre os que temos link); um link de `/p/MLB...` abre o vendedor que
o Mercado Livre escolher naquele instante, e foi assim que um item de
R$ 39,90 virou "R$ 80,00" no story. O painel aceita a URL do anúncio
(medido em 2026-08-28: `produto.mercadolivre.com.br/MLB-7080290072-_JM`
cunhou `https://meli.la/2WFwu8s`, e o link abre o card DAQUELE anúncio com o
mesmo preço que `/products/{id}/items` dá para ele).

O link curto NÃO é construtível: o "link completo" do painel é
`mercadolivre.com.br/social/{apelido}?matt_word=&matt_tool=&ref=` e o `ref` é
um token opaco do servidor, sem o id do anúncio dentro. Cada link precisa ser
cunhado pelo painel — por isso a geração continua sendo em lote, pela
interface, pelo skill `/meli-links-refresh`.

Uso típico: skill `/meli-links-refresh` (`.claude/skills/meli-links-refresh/`).
"""

import json
import re
from datetime import date
from pathlib import Path

import httpx

CREATE_LINK_URL = "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"
# `/products/{id}/items` NÃO traz `permalink` (medido) e `GET /items/{id}` é
# 403 para o nosso token de aplicação: a URL do anúncio é montada.
ITEM_URL_TMPL = "https://produto.mercadolivre.com.br/MLB-{numero}-_JM"

# Casa `MLB123` (URL de produto) E `MLB-123` (URL de anúncio) — sem o hífen
# opcional, TODO link cunhado para um anúncio seria descartado em silêncio no
# casamento da resposta.
_MLB_RE = re.compile(r"MLB-?(\d+)")

# Versão do formato de `data/meli_links.json`. 1 = `{product_id: link}` (fase
# 5C, link por produto); 2 = link por ANÚNCIO (fase 5M).
FORMATO_ATUAL = 2

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def item_url(item_id: str) -> str:
    """URL pública do anúncio, a partir do `item_id` de
    `/products/{id}/items` (com ou sem o prefixo `MLB`)."""
    return ITEM_URL_TMPL.format(numero=str(item_id).replace("MLB", "").lstrip("-"))


def gerar_links(item_ids: list[str], tag: str, cookies: str, csrf: str,
                 client: httpx.Client | None = None,
                 lote: int = 50) -> tuple[dict[str, str], str | None]:
    """Gera links de afiliado de ANÚNCIOS em lote pelo painel do Mercado Livre.

    Devolve `(links, erro)`: `links` mapeia `item_id -> short_url` de
    TODOS os lotes processados com sucesso até o momento (ignora, dentro de
    cada lote, entradas com `created` falso ou sem `short_url`) — nunca é
    descartado por causa de uma falha posterior, mesmo parcial. `erro` é
    `None` quando todos os lotes foram processados sem sinal de sessão
    inválida; caso contrário traz uma mensagem legível.

    Ao detectar sessão expirada/inválida — HTTP 401/403, erro de rede, ou um
    lote inteiro sem sucesso (`total_success == 0 and total_error > 0`, ex.:
    CSRF/cookie ruim) — PARA de processar os lotes restantes (eles teriam a
    mesma sessão e falhariam do mesmo jeito) e devolve `links` com tudo que
    já foi coletado dos lotes anteriores, junto com `erro`: o chamador grava
    o parcial no pool e avisa o usuário, em vez de perder trabalho já feito.
    Nunca levanta.

    Divide `item_ids` em lotes de `lote` itens (uma chamada HTTP por lote) e
    casa a resposta pelo `origin_url` (extraindo o `MLB...` final) para montar
    o dicionário de volta.
    """
    ids = [str(p) for p in dict.fromkeys(item_ids) if p]
    if not ids:
        return {}, None

    own_client = client is None
    client = client or httpx.Client(timeout=30)
    headers = {
        "content-type": "application/json",
        "cookie": cookies,
        "x-csrf-token": csrf,
        "origin": "https://www.mercadolivre.com.br",
        "referer": "https://www.mercadolivre.com.br/afiliados/linkbuilder",
        "user-agent": DEFAULT_USER_AGENT,
    }
    lotes = [ids[i:i + lote] for i in range(0, len(ids), lote)]

    links: dict[str, str] = {}
    erro: str | None = None
    lotes_sem_resposta_valida = 0
    try:
        for lote_ids in lotes:
            urls = [item_url(iid) for iid in lote_ids]
            body = json.dumps({"urls": urls, "tag": tag})
            try:
                r = client.post(CREATE_LINK_URL, content=body, headers=headers)
            except httpx.HTTPError as exc:
                erro = f"meli: erro de rede ao gerar links: {exc}"
                break

            if r.status_code in (401, 403):
                erro = (
                    f"meli: sessão do painel de afiliados expirada ou inválida "
                    f"(HTTP {r.status_code}) — recolete cookie/x-csrf-token")
                break
            if r.status_code != 200:
                lotes_sem_resposta_valida += 1
                continue
            try:
                data = r.json()
            except ValueError:
                lotes_sem_resposta_valida += 1
                continue
            if not isinstance(data, dict):
                # JSON válido mas não-dict (uma lista, uma string): sem esta
                # guarda o data.get() abaixo levantaria AttributeError e
                # furaria o contrato "gerar_links nunca levanta".
                lotes_sem_resposta_valida += 1
                continue

            for item in data.get("urls") or []:
                if not item.get("created") or not item.get("short_url"):
                    continue
                matches = _MLB_RE.findall(str(item.get("origin_url") or ""))
                if not matches:
                    continue
                links["MLB" + matches[-1]] = item["short_url"]

            total_success = data.get("total_success") or 0
            total_error = data.get("total_error") or 0
            if total_success == 0 and total_error > 0:
                erro = (
                    "meli: sessão do painel de afiliados expirada ou inválida "
                    "— um lote inteiro falhou (confira cookie/x-csrf-token e a tag)")
                break
    finally:
        if own_client:
            client.close()

    if erro is None and not links and lotes and lotes_sem_resposta_valida == len(lotes):
        erro = ("meli: nenhum lote teve resposta válida — verifique conectividade "
                "ou tente novamente")
    return links, erro


# -- o pool de links (data/meli_links.json) -----------------------------------


def ler_pool(path: str | Path) -> dict[str, dict]:
    """Lê `data/meli_links.json` e devolve
    `{product_id: {"items": {item_id: link}, "product_link": str}}`.

    Arquivo ausente, JSON inválido ou JSON que não é objeto → `{}` (o pipeline
    trata como "sem link" e descarta a oferta; nunca levanta).

    **Formato antigo (versão 1, `{product_id: link}`) é migrado na leitura**:
    o link vira `product_link` e o produto fica com ZERO anúncios linkados. Os
    55 links da fase 5C continuam guardados — eles são válidos e foram
    trabalho de painel —, mas não servem para publicar preço: eles abrem a
    página de catálogo, onde o Mercado Livre escolhe o vendedor, e é
    exatamente essa escolha que pôs R$ 80,00 num item de R$ 39,90.
    """
    caminho = Path(path)
    try:
        raw = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    if raw.get("version") == FORMATO_ATUAL:
        produtos = raw.get("products")
        entradas = produtos.items() if isinstance(produtos, dict) else []
        return {str(pid): _entrada(valor) for pid, valor in entradas}
    # Versão 1: {product_id: link}
    return {str(pid): {"items": {}, "product_link": str(link)}
            for pid, link in raw.items() if link}


def _entrada(valor) -> dict:
    if not isinstance(valor, dict):
        return {"items": {}, "product_link": ""}
    itens = valor.get("items")
    return {
        "items": ({str(k): str(v) for k, v in itens.items() if v}
                  if isinstance(itens, dict) else {}),
        "product_link": str(valor.get("product_link") or ""),
    }


def escrever_pool(path: str | Path, produtos: dict[str, dict], tag: str = "") -> None:
    """Grava o pool no formato por anúncio, com as chaves ORDENADAS: o diff de
    um refresh mostra o que mudou, não o que reordenou."""
    corpo = {
        "version": FORMATO_ATUAL,
        "generated_at": date.today().isoformat(),
        "tag": tag,
        "products": {pid: {"items": dict(sorted(_entrada(e)["items"].items())),
                           "product_link": _entrada(e)["product_link"]}
                     for pid, e in sorted(produtos.items())},
    }
    Path(path).write_text(json.dumps(corpo, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
