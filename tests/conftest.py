import pytest

from afiliado import cli


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "dotenv_real: o teste exercita o próprio load_dotenv e não deve ser neutralizado",
    )


@pytest.fixture(autouse=True)
def sem_dotenv_do_desenvolvedor(request, monkeypatch):
    """Impede que o `.env` real da máquina vaze para os testes.

    `cli.main` chama `load_dotenv()` com precedência sobre o ambiente (para o
    .env do projeto ganhar de variáveis globais da máquina). Sem esta trava, um
    teste que faz `monkeypatch.delenv("IG_USER_ID")` teria a variável
    reinjetada pelo .env do desenvolvedor — e a suíte passaria no CI (onde não
    há .env) e falharia localmente, ou vice-versa.

    Testes marcados com `@pytest.mark.dotenv_real` (os que testam a própria
    função) ficam de fora.
    """
    if request.node.get_closest_marker("dotenv_real"):
        return
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: 0)


@pytest.fixture(autouse=True)
def sem_agendador_de_verdade(monkeypatch):
    """Nenhum teste consulta o Agendador de Tarefas da máquina (fase 5I).

    `cli.estado_da_tarefa` abre um `powershell` por tarefa. Rodando na máquina
    do dono — que é justamente onde a produção mora — a suíte inteira passaria
    a depender do estado real das tarefas dele, e um `afiliado doctor` de teste
    diria ❌ ou ✅ conforme o dia. O dublê responde "Ready"; quem testa o item
    injeta o próprio (ver `_doctor_agendador(consulta=...)`).
    """
    monkeypatch.setattr(cli, "estado_da_tarefa", lambda nome: "Ready")
