"""Fase 5I: a PRODUÇÃO saiu do GitHub Actions e passou para a máquina do dono.

Quem acorda o pipeline agora é o Agendador de Tarefas do Windows, e quem cria
as duas tarefas é `deploy/agendar-windows.ps1`. O que o script promete
(cadência, janela, posts por run) precisa bater com o ritmo do `config.yaml` e
com os números do `pipeline` — é a mesma trava que existia para o cron, no
lugar novo.

Nenhum teste aqui consulta o Agendador de verdade nem cria tarefa nenhuma: o
que se lê é o TEXTO do script.
"""

import re
from datetime import datetime, timedelta

import yaml

from afiliado import cli, pipeline

SCRIPT = "deploy/agendar-windows.ps1"


def _script() -> str:
    # `utf-8-sig`: o arquivo tem BOM de propósito. O Windows PowerShell 5.1 lê
    # .ps1 SEM BOM como ANSI, e todo acento das mensagens do script viraria
    # mojibake no terminal do dono.
    with open(SCRIPT, encoding="utf-8-sig") as f:
        return f.read()


def _config() -> dict:
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _param(nome: str) -> str:
    """O valor DEFAULT de um parâmetro do `param(...)` do script."""
    achado = re.search(rf"\${nome}\s*=\s*\"?([^\"\r\n,)]+)\"?", _script())
    assert achado, f"parâmetro ${nome} não encontrado em {SCRIPT}"
    return achado.group(1).strip()


def _hora(hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(2026, 8, 26, h, m)


def _disparos(inicio: str, cadencia: str = "CadenciaMinutos") -> list[datetime]:
    """Todos os disparos de um dia: do minuto de início até o fim da janela,
    de `cadencia` em `cadencia` minutos."""
    passo = timedelta(minutes=int(_param(cadencia)))
    fim, agora = _hora(_param("FimDaJanela")), _hora(inicio)
    horarios = []
    while agora <= fim:
        horarios.append(agora)
        agora += passo
    return horarios


INICIOS = ("InicioRun", "InicioStories", "InicioFeed", "InicioFlagrante")


def _orcamentos(disparos: list[datetime]) -> list[int]:
    cfg = _config()
    teto = cfg["channels"]["telegram"]["max_per_day"]
    horario = pipeline.schedule_settings(cfg)
    return [pipeline.pacing_budget(teto, t, horario["window_start"],
                                   horario["window_end"]) for t in disparos]


# -- o que as tarefas são -----------------------------------------------------

def test_o_script_cria_as_tarefas_da_producao():
    """`afiliado run` (as 60 ofertas do dia) e `afiliado stories` (o story com
    figurinha, que NÃO pode rodar no Actions: IP de datacenter diferente a cada
    execução é o padrão que mais dispara `challenge_required`)."""
    texto = _script()
    assert _param("TarefaRun") == "FiscalDaPromo-Run"
    assert _param("TarefaStories") == "FiscalDaPromo-Stories"
    assert "run --posts-per-run" in texto
    assert "stories --posts" in texto


def test_as_pecas_de_feed_tambem_ganham_agendador():
    """O ÚNICO lugar que chamava `afiliado feed` era o passo "Conteúdo do feed"
    do publish.yml. Desligar o `schedule:` de lá sem agendar as duas peças
    mataria o carrossel do termômetro e o flagrante EM SILÊNCIO — e silêncio é
    exatamente o defeito que esta fase existe para acabar.

    Duas tarefas, e não um `cmd /c` encadeado: uma falha não pode derrubar a
    outra, e o `doctor` precisa poder nomear qual das peças ficou sem
    agendador."""
    texto = _script()
    assert _param("TarefaFeed") == "FiscalDaPromo-Feed"
    assert _param("TarefaFlagrante") == "FiscalDaPromo-Flagrante"
    assert "feed --tipo termometro" in texto
    assert "feed --tipo flagrante" in texto
    # Uma por dia é garantia do CÓDIGO; a cadência aqui é só retentativa.
    assert callable(cli._carrossel_pode_sair) and callable(cli._flagrante_pode_sair)
    assert int(_param("CadenciaFeedMinutos")) >= int(_param("CadenciaMinutos"))
    assert len(_disparos(_param("InicioFeed"), "CadenciaFeedMinutos")) >= 5


def test_o_doctor_procura_exatamente_as_tarefas_que_o_script_cria():
    """O nome da tarefa é o contrato entre os dois lados. Se um mudar sozinho,
    o `doctor` passa a dizer "não existe" para uma tarefa que existe — que é
    pior do que não checar nada."""
    for nome in cli.TAREFAS_DA_PRODUCAO:
        assert nome in _script()
    assert set(cli.TAREFAS_DA_PRODUCAO) == {_param("TarefaRun"), _param("TarefaStories"),
                                            _param("TarefaFeed"), _param("TarefaFlagrante")}
    assert cli.SCRIPT_DO_AGENDADOR == SCRIPT


# -- a cadência (T2): 60/dia distribuídas, com folga para disparos perdidos ----

def test_a_cadencia_fecha_o_dia_de_60_ofertas():
    """A conta que escolheu a cadência: `pacing_budget` distribui o teto pela
    janela, e o ÚLTIMO disparo do dia precisa alcançar o teto inteiro — senão
    a meta de 60/dia é inalcançável por construção (o menor da revisão da 5C,
    que a mudança de host podia reintroduzir sem ninguém notar)."""
    teto = _config()["channels"]["telegram"]["max_per_day"]
    for inicio in (_param("InicioRun"), _param("InicioStories")):
        assert _orcamentos(_disparos(inicio))[-1] == teto, inicio


def test_posts_por_run_cobre_dois_disparos_perdidos():
    """Numa máquina que pode dormir, travar ou ser desligada, um disparo
    perdido é rotina — e o dia tem de fechar assim mesmo. Com a cadência fina
    o maior salto do ritmo é 1, então `--posts-per-run` precisa valer pelo
    menos 3 (o salto deste disparo mais os dois que não vieram)."""
    orcamentos = _orcamentos(_disparos(_param("InicioRun")))
    maior_salto = max(b - a for a, b in zip(orcamentos, orcamentos[1:]))
    assert maior_salto == 1
    assert int(_param("PostsPorRun")) >= 3 * maior_salto


def test_o_limiar_do_buraco_na_cadencia_conversa_com_o_agendador():
    """Fase 5G (G3): o aviso de buraco compara o intervalo entre dois runs com
    um número do config — o código não tem como ler o agendador. Era o cron do
    GitHub; agora é o Agendador do Windows, e a trava que obriga os dois a
    mudarem juntos continua sendo esta."""
    cadencia = int(_param("CadenciaMinutos"))
    assert cadencia == pipeline.CADENCIA_MINUTOS
    assert _config()["schedule"]["max_gap_minutes"] == pipeline.DEFAULT_MAX_GAP_MINUTES
    # Tolera UM disparo perdido e acusa a partir do segundo.
    assert 2 * cadencia <= pipeline.max_gap_minutes(_config()) < 4 * cadencia


def test_a_janela_do_agendador_e_a_janela_do_ritmo():
    """Duas janelas divergentes dariam disparos fora do ritmo (orçamento 0) ou
    ritmo sem disparo. O fim é o mesmo `schedule.window_end`; o COMEÇO é alguns
    minutos depois de `window_start` de propósito (ver o teste do minuto
    irregular)."""
    horario = pipeline.schedule_settings(_config())
    assert _param("FimDaJanela") == horario["window_end"]
    for nome in INICIOS:
        inicio = _param(nome)
        assert _hora(inicio) >= _hora(horario["window_start"]), nome
        assert pipeline.pacing_budget(60, _hora(inicio), horario["window_start"],
                                      horario["window_end"]) > 0, nome


def test_o_minuto_de_inicio_e_irregular_e_as_tarefas_nao_colidem():
    """O runbook do instagrapi já pedia: 60 stories no minuto zero de cada hora
    parece robô. E duas tarefas não podem acordar no MESMO instante — são dois
    processos Python disputando a mesma máquina e as mesmas APIs."""
    minutos = {int(_param(p).split(":")[1]) for p in INICIOS}
    assert 0 not in minutos
    assert len(minutos) == len(INICIOS)
    cadencia = int(_param("CadenciaMinutos"))
    # E nem por acaso: a diferença entre os inícios das duas tarefas de mesma
    # cadência não é múltipla dela, senão elas se encontrariam em todo disparo.
    diferenca = abs(_hora(_param("InicioRun")) - _hora(_param("InicioStories")))
    assert diferenca.total_seconds() // 60 % cadencia != 0


# -- as condições da tarefa (T1) ----------------------------------------------

def test_a_tarefa_inicia_na_pasta_do_projeto():
    """`config.yaml`, `.env` e `data/` são lidos por caminho RELATIVO: uma
    tarefa sem diretório de trabalho roda em C:\\Windows\\System32 e o pipeline
    sobe sem config, sem credencial e com um banco de estado vazio."""
    assert "WorkingDirectory" in _script()
    assert "$ProjetoDir" in _script()


def test_um_run_travado_nao_empilha_dez():
    assert "IgnoreNew" in _script()


def test_a_tarefa_roda_com_a_maquina_ocupada_e_na_bateria():
    """Os dois padrões do Agendador que fazem a tarefa NÃO rodar em silêncio:
    "iniciar somente se ocioso" (a máquina do dono é usada o dia todo) e parar
    quando sai da tomada."""
    texto = _script()
    assert "-RunOnlyIfIdle:$false" in texto
    assert "-AllowStartIfOnBatteries" in texto
    assert "-DontStopIfGoingOnBatteries" in texto


def test_o_script_e_idempotente():
    """Rodar de novo ATUALIZA, não duplica: `Register-ScheduledTask -Force`
    sobrescreve a tarefa de mesmo nome."""
    assert "Register-ScheduledTask" in _script()
    assert "-Force" in _script()


def test_o_script_desfaz_com_remover():
    texto = _script()
    assert "[switch]$Remover" in texto
    assert "Unregister-ScheduledTask" in texto


def test_o_script_falha_alto_quando_o_exe_ou_a_pasta_nao_existem():
    """Uma tarefa apontando para um `afiliado.exe` que não existe é criada sem
    reclamar e falha, em silêncio, a cada 15 minutos — o modo de falha que esta
    fase inteira existe para acabar."""
    texto = _script()
    assert texto.count("throw") >= 2
    assert "Test-Path" in texto


# -- o runbook (T5) ------------------------------------------------------------

def _runbook() -> str:
    with open(cli.RUNBOOK_DA_PRODUCAO, encoding="utf-8") as f:
        return " ".join(f.read().split())      # a quebra do markdown não conta


def test_o_runbook_da_producao_diz_a_ordem_da_virada():
    """Invertida, a ordem deixa um intervalo sem ninguém publicando — e o
    posto duplo, se ninguém desligar o outro lado, publica a mesma oferta duas
    vezes (cada host tem o seu `state.db`, e é ele que guarda o dedupe)."""
    runbook = _runbook()
    assert "A ORDEM DA VIRADA" in runbook
    assert runbook.index("Criar as tarefas") < runbook.index("SÓ ENTÃO")
    assert "Ver um run REAL acontecer" in runbook
    assert "Nunca deixe os dois publicando ao mesmo tempo" in runbook


def test_o_runbook_diz_o_pre_requisito_que_o_dono_faz_uma_vez():
    """`pip install -e .` na pasta principal (a instalação editável aponta para
    o worktree) e `afiliado ig-login` (a sessão do instagrapi é gitignored e só
    existe lá). Sem os dois, as tarefas rodam código velho ou não logam."""
    runbook = _runbook()
    assert "pip install -e ." in runbook and "afiliado ig-login" in runbook
    assert "worktree" in runbook


def test_o_runbook_ensina_a_conferir_e_a_voltar_para_o_actions():
    runbook = _runbook()
    assert "Como conferir que rodou" in runbook
    assert "Get-ScheduledTask" in runbook
    assert "Como voltar para o Actions" in runbook
    # Voltar sem desligar as tarefas é posto duplo: a ordem inversa também
    # precisa estar escrita.
    assert "-Remover" in runbook


def test_os_runbooks_nao_divergem_sobre_como_agendar():
    """A seção "Agendar no Windows" do runbook do instagrapi era um
    procedimento MANUAL escrito à mão, com outra cadência. Dois procedimentos
    divergentes é como o dono acaba com uma tarefa que ninguém sabe de onde
    veio."""
    with open("docs/runbooks/instagrapi-stories.md", encoding="utf-8") as f:
        instagrapi = " ".join(f.read().split())
    assert SCRIPT.replace("/", "\\") in instagrapi or SCRIPT in instagrapi
    assert cli.RUNBOOK_DA_PRODUCAO.split("/")[-1] in instagrapi
    # E o passo a passo manual (Criar Tarefa -> Disparadores -> Ações) saiu.
    assert "Criar Tarefa" not in instagrapi


def test_o_readme_diz_onde_a_producao_roda_agora():
    with open("README.md", encoding="utf-8") as f:
        readme = " ".join(f.read().split())
    assert "Agendador de Tarefas do Windows" in readme
    assert cli.RUNBOOK_DA_PRODUCAO in readme
    assert "fallback manual" in readme.lower()


def test_o_script_nao_grava_credencial_nenhuma():
    """As tarefas herdam o `.env` da pasta do projeto. Senha de conta de
    serviço, `-Password`, token ou usuário do Instagram gravados aqui virariam
    credencial em texto puro dentro do agendador — e no git."""
    texto = _script()
    for proibido in ("-Password", "IG_PASSWORD", "IG_USERNAME", "SHOPEE_APP_SECRET",
                     "TELEGRAM_BOT_TOKEN", "ConvertTo-SecureString"):
        assert proibido not in texto, proibido


def test_a_tarefa_roda_sem_janela_e_sem_guardar_senha():
    """`S4U`, não `Interactive` (2026-08-28, pedido do dono).

    O `afiliado.exe` é aplicação de console: no modo interativo o Agendador
    abria uma janela de terminal na tela a cada 15 minutos. `S4U` roda em
    sessão NÃO interativa — sem janela — e é, como o `Interactive`, aceito sem
    guardar senha, que é a outra propriedade que não pode cair.

    O que este teste protege de verdade: voltar para `Interactive` por engano
    devolve a janela, e ninguém liga uma coisa à outra seis semanas depois."""
    texto = _script()
    assert "-LogonType S4U" in texto, "a tarefa voltou a abrir janela"
    assert "-LogonType Interactive" not in texto
    # Sem senha: nenhuma das formas de passar credencial pode aparecer.
    for proibido in ("-Password", "-User ", "ConvertTo-SecureString"):
        assert proibido not in texto, f"o script passou a lidar com senha: {proibido!r}"


def test_a_janela_de_publicacao_nao_depende_do_usuario_estar_logado():
    """Consequência boa do S4U que vale afirmar: o script não promete mais
    "só roda com o usuário conectado". Se a frase voltar, o runbook e o
    comportamento discordam."""
    texto = _script().lower()
    assert "só rodam com o usuário conectado" not in texto
