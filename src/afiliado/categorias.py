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
