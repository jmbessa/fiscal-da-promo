import pytest

from afiliado import cli, pricing


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


@pytest.fixture(params=[True, False], ids=["rotulo_ligado", "rotulo_desligado"])
def rotulo(request, monkeypatch):
    """Roda o teste nos DOIS estados de `pricing.MOSTRAR_SEM_CUPOM` (fase 5N)
    e devolve o rótulo esperado numa oferta da SHOPEE — `pricing.SEM_CUPOM`
    com o interruptor ligado, "" com ele desligado. Fora da Shopee é "" nos
    dois estados, e é isso que cada teste que usa a fixture afirma.

    Ela existe para os testes SOBRE o rótulo afirmarem a REGRA (ligado, só a
    Shopee; desligado, ninguém — nem a Shopee) em vez do estado do
    interruptor: 20 testes deste projeto quebraram de uma vez quando o dono
    mexeu nos canais do `config.yaml`, porque afirmavam a configuração.

    Quem só publica preço no meio de um texto ou de uma arte NÃO usa esta
    fixture: afirma o padrão vigente (hoje, sem rótulo) e pronto — ler os
    testes tem de mostrar o que a peça publica agora.

    O monkeypatch é da CONSTANTE porque só `sem_cupom` recebe `mostrar=`;
    `preco_publicado`, `price_line`, a arte e as legendas leem a constante.
    """
    monkeypatch.setattr(pricing, "MOSTRAR_SEM_CUPOM", request.param)
    return pricing.SEM_CUPOM if request.param else ""
