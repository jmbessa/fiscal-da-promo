"""As unidades systemd e o script de instalação são texto — mas o que eles
afirmam sobre a operação (janela, falha notificada, variáveis do .env)
precisa bater com o código. Estes testes travam o que a fase 5A mudou."""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def _unidade(nome: str) -> dict[str, str]:
    pares = {}
    for linha in (DEPLOY / nome).read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith(("#", "[")) and "=" in linha:
            chave, _, valor = linha.partition("=")
            pares[chave.strip()] = valor.strip()
    return pares


def test_timer_nao_dispara_run_atrasado_fora_da_janela():
    # Persistent=true disparava um run às 03:00 ao religar a VPS.
    assert _unidade("afiliado.timer")["Persistent"] == "false"
