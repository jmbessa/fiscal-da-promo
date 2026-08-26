import pytest

from afiliado.config import load_config


def test_load_config_reads_project_yaml():
    cfg = load_config("config.yaml")
    assert cfg["selection"]["posts_per_run"] == 1
    assert cfg["llm"]["model"] == "haiku"
    # Fase 3 (correção pós-revisão): category_ids por fonte e allowed_domains
    # com os domínios do ML — sem isso o ML publica zero ofertas em silêncio.
    assert "meli" in cfg["selection"]["category_ids"]
    assert "mercadolivre.com" in cfg["validation"]["allowed_domains"]


def test_config_carrega_a_descoberta_medida_em_2026_08_26():
    """Fase 5C (C1): o que a medição de 147 chamadas reais recomendou tem de
    estar no config, senão o volume volta a 8 posts/dia sustentáveis."""
    sh = load_config("config.yaml")["shopee"]
    assert sh["pages"] == 40                  # a janela real por listagem
    assert sh["page_size"] == 50              # limit máximo aceito
    assert sh["sort_types"] == [2] and sh["list_type"] == 0
    assert sh["calls_per_run"] == 8
    assert sh["candidate_max_age_days"] == 3
    assert len(sh["category_ids"]) == 5
    assert len(sh["subcategory_ids"]) == 26   # as de >= 25 itens no top-500 da raiz
    assert sh["subcategory_first_page"] == 2  # a p1 da subcategoria ≈ topo da raiz
    termos = [t for lista in sh["keywords"].values() for t in lista]
    assert len(termos) == 40 == len(set(termos))
    assert set(sh["keywords"]) == set(load_config("config.yaml")["selection"]
                                      ["category_ids"]["shopee"])


def test_load_config_rejects_missing_keys(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("llm:\n  model: haiku\n", encoding="utf-8")
    with pytest.raises(ValueError, match="obrigat"):
        load_config(p)
