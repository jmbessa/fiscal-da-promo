"""Geração de links de afiliado em lote pelo painel do Mercado Livre.

Não existe API pública para isso — o endpoint usado aqui é interno do
painel (`/afiliados/linkbuilder`), autenticado por sessão via cookies do
navegador (não por OAuth/Bearer, diferente do resto de `sources/meli.py`).
Testado contra o endpoint real: aceita lote (múltiplos produtos numa
chamada), é idempotente por (produto, etiqueta) — repetir devolve o mesmo
link curto do painel — e a etiqueta (`tag`) precisa existir previamente no
painel de afiliados, senão o item falha (`total_error`).

Uso típico: skill `/meli-links-refresh` (`.claude/skills/meli-links-refresh/`).
"""

import json
import re

import httpx

CREATE_LINK_URL = "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"
PRODUCT_URL_TMPL = "https://www.mercadolivre.com.br/p/{product_id}"

_MLB_RE = re.compile(r"MLB\d+")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def gerar_links(product_ids: list[str], tag: str, cookies: str, csrf: str,
                 client: httpx.Client | None = None,
                 lote: int = 50) -> tuple[dict[str, str], str | None]:
    """Gera links de afiliado em lote pelo painel do Mercado Livre.

    Devolve `(links, erro)`: `links` mapeia `product_id -> short_url` de
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

    Divide `product_ids` em lotes de `lote` itens (uma chamada HTTP por
    lote) e casa a resposta pelo `origin_url` (extraindo o `MLB...` final)
    para montar o dicionário de volta.
    """
    ids = [str(p) for p in dict.fromkeys(product_ids) if p]
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
            urls = [PRODUCT_URL_TMPL.format(product_id=pid) for pid in lote_ids]
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

            for item in data.get("urls") or []:
                if not item.get("created") or not item.get("short_url"):
                    continue
                matches = _MLB_RE.findall(str(item.get("origin_url") or ""))
                if not matches:
                    continue
                links[matches[-1]] = item["short_url"]

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
