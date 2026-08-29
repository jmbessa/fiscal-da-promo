"""O leitor das respostas cruas do JoomPulse (fase 5R).

O formato que o conector devolve é COLUNAR — `columns` mais `data` com uma
lista por linha —, e não a lista de dicionários que a fase 5O supôs. Medido em
2026-08-29 contra `ShbMartItem`; ver o relatório da fase.
"""

import json

import pytest

from afiliado import joompulse

COLUNAR = {
    "columns": ["itemId", "itemLastSeenDate", "price"],
    "data": [[8812570518, "2026-08-23T17:08:46.424", 90.55],
             [16603921155, "2026-08-27T16:12:59.745", 42.21]],
    "dimensionCount": 2,
    "lastRefreshTime": "2026-08-29T18:29:05.250Z",
    "totalRows": 2,
    "types": ["number", "time", "number"],
}


def test_le_o_formato_colunar_que_o_conector_devolve():
    linhas, limite = joompulse.linhas(COLUNAR)
    assert linhas == [
        {"itemId": 8812570518, "itemLastSeenDate": "2026-08-23T17:08:46.424", "price": 90.55},
        {"itemId": 16603921155, "itemLastSeenDate": "2026-08-27T16:12:59.745", "price": 42.21},
    ]
    assert limite == joompulse.LIMITE_PADRAO


def test_le_a_lista_de_dicionarios_e_a_lista_nua():
    dicionarios = {"data": [{"ShbMartItem.itemId": "1"}]}
    assert joompulse.linhas(dicionarios)[0] == [{"ShbMartItem.itemId": "1"}]
    assert joompulse.linhas([{"a": 1}])[0] == [{"a": 1}]


def test_o_limite_sai_da_consulta_quando_ela_foi_salva_junto():
    assert joompulse.linhas({"query": {"limit": 37}, "data": []})[1] == 37
    assert joompulse.linhas({"query": {"limit": "lixo"}, "data": []})[1] == joompulse.LIMITE_PADRAO
    assert joompulse.linhas({"query": "nem é objeto", "data": []})[1] == joompulse.LIMITE_PADRAO


def test_linha_colunar_mais_curta_que_as_colunas_nao_inventa_campo():
    # `zip` truncaria em silêncio; o que falta tem de FALTAR, para o leitor de
    # cada cubo recusar a linha com motivo em vez de ler o campo do vizinho.
    bruto = {"columns": ["a", "b", "c"], "data": [[1, 2]]}
    assert joompulse.linhas(bruto)[0] == [{"a": 1, "b": 2}]


def test_bruto_vazio_ou_estranho_nao_levanta():
    for bruto in (None, {}, {"data": None}, {"columns": ["a"], "data": None}, "texto", 7):
        assert joompulse.linhas(bruto)[0] == []


def test_campo_aceita_o_nome_com_e_sem_o_prefixo_do_cubo():
    assert joompulse.campo({"ShbMartItem.price": 1}, "ShbMartItem", "price") == 1
    assert joompulse.campo({"price": 2}, "ShbMartItem", "price") == 2
    assert joompulse.campo({}, "ShbMartItem", "price") is None


def test_dia_aceita_data_nua_e_carimbo_de_tempo():
    from datetime import date
    assert joompulse.dia("2026-08-28") == date(2026, 8, 28)
    assert joompulse.dia("2026-08-28T17:08:46.424") == date(2026, 8, 28)
    assert joompulse.dia(date(2026, 8, 28)) == date(2026, 8, 28)
    assert joompulse.dia("não é data") is None
    assert joompulse.dia(None) is None


def test_carrega_arquivos_e_diretorios(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps(COLUNAR), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps({"data": []}), encoding="utf-8")
    solto = tmp_path / "solto.txt"
    solto.write_text(json.dumps({"data": [1]}), encoding="utf-8")
    brutos = joompulse.carrega([str(tmp_path), str(solto)])
    assert len(brutos) == 3
    assert brutos[0] == {"data": []}          # o diretório sai ordenado: a.json antes de b.json


def test_carrega_recusa_arquivo_ausente(tmp_path):
    with pytest.raises(OSError):
        joompulse.carrega([str(tmp_path / "não existe.json")])
