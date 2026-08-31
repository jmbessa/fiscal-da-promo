"""Nome legível das categorias RAIZ que o projeto usa (fase 5D).

`Offer.category` é um ID — "100630" na Shopee, "MLB1246" no Mercado Livre —, e
ID não serve para o que a legenda precisa: desde 10/07/2025 os posts do
Instagram são indexados pelo Google, e a legenda é uma página de busca.
"Categoria 100630" não é uma busca que alguém faz.

O mapa é DELIBERADAMENTE pequeno: só as raízes que `selection.category_ids`
permite (as cinco da Shopee, escolhidas na análise de 2026-08-23) e as que
aparecem no pool curado do ML. ID desconhecido devolve "" e a linha some da
legenda — inventar um nome de categoria seria escrever na legenda pública algo
que não medimos.
"""

NOMES = {
    # Shopee — as raízes de `selection.category_ids` (ver config.yaml).
    "100630": "Beleza",
    "100636": "Casa",
    "100001": "Saúde",
    "100637": "Esportes",
    "100632": "Mãe e Bebê",
    # Mercado Livre — as raízes presentes em `data/meli_offers.json`.
    "MLB1246": "Beleza e Cuidado Pessoal",
    "MLB1276": "Esportes e Fitness",
    "MLB1574": "Casa, Móveis e Decoração",
    "MLB264586": "Saúde",
}


def nome(category_id: str) -> str:
    """O nome da categoria, ou "" quando o ID não está no mapa."""
    return NOMES.get(str(category_id or "").strip(), "")


# --- Fase 5U (U4): hashtags ---------------------------------------------------
#
# Nenhum dos 5 posts publicados até 2026-08-29 tinha hashtag, e para uma conta
# sem audiência isso é um sinal de categorização a menos: a hashtag é o que diz
# ao Instagram DE QUE o post é, para quem ainda não segue.
#
# O mapa mora no `config.yaml` (seção `hashtags:`), e não aqui, pelo mesmo
# motivo de `selection.category_ids`: é dado de negócio, muda com a estratégia
# e ninguém revisa uma lista escondida no código.

# De 4 a 6 derivadas da categoria. O teto não é estética: bloco de 30 hashtags
# genéricas é o padrão que o Instagram trata como spam, e ele dilui o sinal que
# as poucas certas dão.
MAX_DERIVADAS = 6
# Duas fixas da marca, e só duas — elas são IDENTIDADE, não alcance. Uma lista
# grande de tags de marca numa conta com 2 seguidores não é encontrada por
# ninguém e come o espaço das que são.
MAX_FIXAS = 2


def _tag(bruta) -> str:
    """"beleza", "#beleza", "##beleza", " cuidado pessoal " -> "#beleza",
    "#cuidadopessoal". Hashtag com espaço não é hashtag, e "#" duplicado quebra
    a busca do Instagram."""
    texto = "".join(str(bruta or "").split()).lstrip("#")
    return f"#{texto}" if texto else ""


def hashtags(secao, categories) -> list[str]:
    """As hashtags que fecham a legenda: as da(s) categoria(s) do post,
    seguidas das fixas da marca.

    `secao` é `config["hashtags"]` (`{fixas: [...], por_categoria: {id: [...]}}`).
    Categoria desconhecida NÃO quebra o post e NÃO inventa tag: cai só nas
    fixas — o ID vem de dados de terceiros, e a Shopee cria categoria nova sem
    avisar ninguém. Seção ausente ou malformada devolve lista vazia, e a
    legenda fica como era antes desta fase.

    Sem repetição e na ordem do config: no carrossel, seis ofertas de duas
    categorias somam as duas listas uma vez só — é UMA legenda, não seis.
    """
    if not isinstance(secao, dict):
        return []
    por_categoria = secao.get("por_categoria")
    por_categoria = por_categoria if isinstance(por_categoria, dict) else {}
    derivadas: list[str] = []
    for cid in categories or []:
        for bruta in por_categoria.get(str(cid or "").strip()) or []:
            tag = _tag(bruta)
            if tag and tag not in derivadas:
                derivadas.append(tag)
    saida = derivadas[:MAX_DERIVADAS]
    for bruta in (secao.get("fixas") or [])[:MAX_FIXAS]:
        tag = _tag(bruta)
        if tag and tag not in saida:
            saida.append(tag)
    return saida


def linha_de_hashtags(secao, categories) -> str:
    """As hashtags numa linha só, ou "" quando não há nenhuma — para quem monta
    legenda não precisar decidir se acrescenta um bloco vazio."""
    return " ".join(hashtags(secao, categories))
