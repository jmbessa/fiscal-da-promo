"""As respostas cruas do JoomPulse, lidas por quem faz conta com elas (fase 5R).

Não há rede aqui — e não pode haver. Quem consulta o conector é um skill, com a
sessão do dono; este módulo lê o JSON que o skill salvou em
`data/joompulse_raw/` e o entrega em linhas. É a mesma disciplina da fase 5O:
a conta mora em código testado, a coleta mora no skill.

**O formato é COLUNAR, e isso é o achado que criou este módulo.** Medido em
2026-08-29 contra `ShbMartItem`, `query_cubejs_shopee` devolve

    {"columns": ["itemId", "price"], "data": [[8812570518, 90.55], ...],
     "dimensionCount": 1, "types": [...], "totalRows": 100,
     "lastRefreshTime": "..."}

— `data` é uma lista de LISTAS, não de dicionários. A fase 5O escreveu
`afiliado.shopee_regua` supondo `{"data": [{...}]}` e filtrando
`isinstance(linha, dict)`: um bruto colunar passaria por ele como ZERO linhas,
e todo item viraria "sem linha no cubo na janela" — uma recusa educada, com
motivo, para um dado que estava lá. Silêncio com nome é melhor que número
inventado, mas continua sendo silêncio. As duas formas passam a ser lidas aqui,
uma vez só, para os dois cubos.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

# O teto de linhas por consulta do Cube.js (medido em 2026-08-28 e reconfirmado
# em 2026-08-29: `limit: 100` devolveu exatamente 100 linhas para 120 ids).
# Vale como palpite quando a resposta salva não traz o `query` que a gerou — e
# a resposta CRUA não traz mesmo: quem salva é que anexa a consulta.
LIMITE_PADRAO = 100

__all__ = ["LIMITE_PADRAO", "linhas", "campo", "dia", "carrega"]


def linhas(bruto) -> tuple[list[dict], int]:
    """`(linhas em dicionário, teto de linhas da consulta)`.

    Aceita as três formas que aparecem em `data/joompulse_raw/`:

    - **colunar** (o que o conector devolve): `columns` + `data` de listas;
    - `{"data": [{...}]}` — a forma que a 5O supôs, e que continua valendo para
      um bruto anotado à mão;
    - a lista nua de dicionários.

    Bruto ausente ou de formato inesperado devolve `([], LIMITE_PADRAO)`: quem
    chama recusa o item com motivo, e nada aqui levanta no meio de uma coleta.
    """
    limite = LIMITE_PADRAO
    if isinstance(bruto, dict):
        consulta = bruto.get("query")
        if isinstance(consulta, dict):
            try:
                limite = int(consulta.get("limit") or LIMITE_PADRAO)
            except (TypeError, ValueError):
                limite = LIMITE_PADRAO
        colunas = bruto.get("columns")
        dados = bruto.get("data")
        if isinstance(colunas, list) and colunas:
            # `zip` sem `strict` truncaria a linha curta em silêncio; o campo
            # que falta tem de FALTAR, para o leitor do cubo recusá-la com
            # motivo em vez de ler o valor da coluna vizinha.
            return ([dict(zip(colunas, linha)) for linha in dados or []
                     if isinstance(linha, (list, tuple))], limite)
        bruto = dados
    if not isinstance(bruto, list):
        return [], limite
    return [linha for linha in bruto if isinstance(linha, dict)], limite


def campo(linha: dict, cubo: str, nome: str):
    """O valor de `nome` na linha, com ou sem o prefixo do cubo.

    O Cube.js devolve `ShbMartItem.price` quando a consulta mistura cubos e
    `price` quando não — e as duas formas existem em `data/joompulse_raw/`."""
    return linha.get(f"{cubo}.{nome}", linha.get(nome))


def dia(valor) -> date | None:
    """A data de um campo de tempo do cubo — `2026-08-28` ou
    `2026-08-28T17:08:46.424`: os 10 primeiros caracteres bastam. None para o
    que não é data, e o `None` é o que faz a linha ser recusada com motivo."""
    if isinstance(valor, date):
        return valor
    if not isinstance(valor, str):
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


def carrega(caminhos: list[str]) -> list:
    """Os JSONs de `data/joompulse_raw/…` — arquivos ou diretórios (nestes, os
    `*.json` em ordem de nome, que é a ordem em que as ondas foram salvas)."""
    arquivos: list[Path] = []
    for caminho in caminhos:
        p = Path(caminho)
        arquivos.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    return [json.loads(a.read_text(encoding="utf-8")) for a in arquivos]
