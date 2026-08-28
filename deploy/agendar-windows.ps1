<#
.SYNOPSIS
    Cria (ou atualiza) as duas tarefas do Agendador de Tarefas do Windows que
    executam a produção do Fiscal da Promo na máquina do dono.

.DESCRIPTION
    Fase 5I. A produção saiu do GitHub Actions por três fatos MEDIDOS:

      1. O agendador do GitHub não entrega: o workflow `publish` teve UM run em
         toda a história do repositório (2026-08-27T20:51Z, 51 min atrasado)
         contra ~16 disparos esperados em 25 h.
      2. Story com figurinha de link NÃO PODE rodar no Actions — IP de
         datacenter, diferente a cada execução, é o padrão que mais dispara
         `challenge_required`, e a Graph API não publica figurinha nenhuma. O
         próprio código recusa (AVISO_STORY_LINK_FORA_DO_RUN).
      3. A máquina do dono é um host viável, medido em 2026-08-28: 48,7 h de
         uptime contínuo, plano de energia "Ultimate Performance" e suspensão
         em corrente alternada = 0 (nunca suspende).

    Duas tarefas, as duas idempotentes (rodar de novo ATUALIZA, não duplica):

      FiscalDaPromo-Run      -> afiliado run --posts-per-run N
      FiscalDaPromo-Stories  -> afiliado stories --posts N

    Nenhuma credencial é gravada aqui. As tarefas rodam como o usuário
    interativo (`LogonType Interactive`), que é o único modo que o Agendador
    aceita SEM guardar senha — e herdam o `.env` da pasta do projeto, que é o
    diretório de trabalho delas.

    A ORDEM DA VIRADA importa e está no runbook
    (docs/runbooks/producao-windows.md): primeiro criar as tarefas e ver um run
    REAL acontecer; só então desligar o `schedule:` do publish.yml. Invertido,
    fica um intervalo sem ninguém publicando.

.PARAMETER ProjetoDir
    Pasta do projeto — de onde saem `config.yaml`, `.env` e `data/`. Padrão: a
    pasta acima desta.

.PARAMETER AfiliadoExe
    Caminho do `afiliado.exe`. Padrão: o que estiver no PATH, senão
    `.venv\Scripts\afiliado.exe` dentro de ProjetoDir.

.PARAMETER Remover
    Apaga as duas tarefas e sai. Não mexe em mais nada.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\agendar-windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\agendar-windows.ps1 -Remover
#>
[CmdletBinding()]
param(
    [string]$ProjetoDir = "",
    [string]$AfiliadoExe = "",
    [string]$TarefaRun = "FiscalDaPromo-Run",
    [string]$TarefaStories = "FiscalDaPromo-Stories",
    # A cadência. 60 ofertas/dia distribuídas em ~15 h pedem uma a cada ~15 min,
    # e é isso que o `pacing_budget` já assume (config.yaml, channels.telegram).
    # Ao mudar este número, mude `schedule.max_gap_minutes` no config.yaml e
    # $PostsPorRun aqui — tests/test_agendador_windows.py trava os três juntos.
    [int]$CadenciaMinutos = 15,
    # Minuto de início IRREGULAR, e diferente entre as duas tarefas: 60 posts no
    # minuto zero de cada hora parece robô, e duas tarefas no mesmo instante são
    # dois processos Python disputando a máquina e as mesmas APIs.
    [string]$InicioRun = "08:03",
    [string]$InicioStories = "08:08",
    # O fim da janela é o `schedule.window_end` do config.yaml. Um disparo
    # depois dele teria orçamento 0 (o ritmo não libera nada fora da janela).
    [string]$FimDaJanela = "23:15",
    # Quanto UM run pode chegar a publicar. Com a cadência de 15 min o maior
    # salto do ritmo é 1: 4 cobre este disparo e mais TRÊS perdidos. O teto
    # diário e o ritmo continuam mandando — isto é só a folga de recuperação.
    [int]$PostsPorRun = 4,
    [switch]$Remover
)

$ErrorActionPreference = "Stop"

$TAREFAS = @($TarefaRun, $TarefaStories)

# -- desfazer ------------------------------------------------------------------

if ($Remover) {
    foreach ($nome in $TAREFAS) {
        if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $nome -Confirm:$false
            Write-Host "removida: $nome"
        }
        else {
            Write-Host "não existia: $nome"
        }
    }
    Write-Host ""
    Write-Host "Sem estas tarefas NINGUÉM chama o pipeline. Se a intenção é voltar"
    Write-Host "para o GitHub Actions, releia docs/runbooks/producao-windows.md:"
    Write-Host 'o "schedule:" do publish.yml precisa ser religado ANTES.'
    return
}

# -- falhar alto, antes de criar qualquer coisa --------------------------------
#
# Uma tarefa que aponta para um caminho que não existe é criada sem reclamar e
# falha, em silêncio, a cada 15 minutos. É exatamente o modo de falha que esta
# fase existe para acabar: o projeto precisa saber dizer "ninguém está me
# chamando".

# O padrão é a pasta acima desta — resolvido AQUI, e não no `param()`: o
# Windows PowerShell 5.1 ainda não populou `$PSScriptRoot` na hora de ligar os
# parâmetros, e o default virava string vazia.
if (-not $ProjetoDir) {
    $ProjetoDir = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { (Get-Location).Path }
}
if (-not (Test-Path -LiteralPath $ProjetoDir -PathType Container)) {
    throw "pasta do projeto não encontrada: $ProjetoDir. Passe -ProjetoDir com o caminho certo."
}
$ProjetoDir = (Resolve-Path -LiteralPath $ProjetoDir).Path

$configYaml = Join-Path $ProjetoDir "config.yaml"
if (-not (Test-Path -LiteralPath $configYaml -PathType Leaf)) {
    throw "não achei config.yaml em $ProjetoDir — esta não parece ser a pasta do projeto."
}

if (-not $AfiliadoExe) {
    $noPath = Get-Command "afiliado.exe" -ErrorAction SilentlyContinue
    if ($noPath) {
        $AfiliadoExe = $noPath.Source
    }
    else {
        $palpite = Join-Path $ProjetoDir ".venv\Scripts\afiliado.exe"
        if (Test-Path -LiteralPath $palpite -PathType Leaf) { $AfiliadoExe = $palpite }
    }
}
if (-not $AfiliadoExe -or -not (Test-Path -LiteralPath $AfiliadoExe -PathType Leaf)) {
    throw ("afiliado.exe não encontrado. Instale o projeto ('pip install -e .') na pasta " +
           "principal e rode de novo, ou passe -AfiliadoExe com o caminho completo.")
}
$AfiliadoExe = (Resolve-Path -LiteralPath $AfiliadoExe).Path

# O `.env` é AVISO, não erro: ele pode não existir ainda na primeira instalação,
# e a tarefa criada continua correta. O que não pode é o dono descobrir isso
# pelo silêncio.
if (-not (Test-Path -LiteralPath (Join-Path $ProjetoDir ".env") -PathType Leaf)) {
    Write-Warning "não achei .env em $ProjetoDir — sem ele as tarefas rodam sem credencial."
}

# -- as peças da tarefa --------------------------------------------------------

function New-GatilhoRepetido {
    <#
        Um gatilho DIÁRIO que se repete a cada $CadenciaMinutos até o fim da
        janela. O Agendador não tem "diário com repetição" numa chamada só: a
        receita é criar o gatilho diário e enxertar nele a `Repetition` de um
        gatilho `-Once`.
    #>
    param([string]$Inicio)

    $comeco = [datetime]::ParseExact($Inicio, "HH:mm", $null)
    $fim = [datetime]::ParseExact($FimDaJanela, "HH:mm", $null)
    $duracao = $fim - $comeco
    if ($duracao.TotalMinutes -lt $CadenciaMinutos) {
        throw "janela inválida: $Inicio até $FimDaJanela não cabe um intervalo de $CadenciaMinutos min."
    }
    $gatilho = New-ScheduledTaskTrigger -Daily -At $comeco
    $gatilho.Repetition = (New-ScheduledTaskTrigger -Once -At $comeco `
            -RepetitionInterval (New-TimeSpan -Minutes $CadenciaMinutos) `
            -RepetitionDuration $duracao).Repetition
    return $gatilho
}

# `RunOnlyIfIdle:$false` e as duas de bateria são os três padrões do Agendador
# que fazem a tarefa não rodar EM SILÊNCIO numa máquina de uso diário.
# `IgnoreNew`: um run travado não pode empilhar dez atrás dele.
# `StartWhenAvailable`: disparo perdido (máquina desligada) sai assim que der.
$configuracao = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfIdle:$false `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

# Sem credencial: `Interactive` é o único modo que o Agendador aceita sem
# guardar senha. O preço é que as tarefas só rodam com o usuário conectado —
# que é o que se quer aqui (IP e sessão residenciais estáveis).
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

function Register-TarefaDoFiscal {
    param([string]$Nome, [string]$Argumentos, [string]$Inicio, [string]$Descricao)

    $acao = New-ScheduledTaskAction -Execute $AfiliadoExe -Argument $Argumentos `
        -WorkingDirectory $ProjetoDir
    # `-Force` é o que torna o script idempotente: mesma tarefa, atualizada.
    Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger (New-GatilhoRepetido $Inicio) `
        -Settings $configuracao -Principal $principal -Description $Descricao -Force | Out-Null
    Write-Host "ok: $Nome — $Inicio, a cada $CadenciaMinutos min até $FimDaJanela"
    Write-Host "    $AfiliadoExe $Argumentos"
    Write-Host "    iniciar em: $ProjetoDir"
}

Register-TarefaDoFiscal -Nome $TarefaRun -Inicio $InicioRun `
    -Argumentos "run --posts-per-run $PostsPorRun" `
    -Descricao ("Fiscal da Promo: publica as ofertas do dia (Telegram + feed do Instagram). " +
                "Criado por deploy/agendar-windows.ps1 — nao edite a mao.")

Register-TarefaDoFiscal -Nome $TarefaStories -Inicio $InicioStories `
    -Argumentos "stories --posts $PostsPorRun" `
    -Descricao ("Fiscal da Promo: story com figurinha de link (instagrapi). NAO roda no " +
                "GitHub Actions. Criado por deploy/agendar-windows.ps1 — nao edite a mao.")

Write-Host ""
Write-Host "Confira com: afiliado doctor   (ele agora checa estas duas tarefas)"
Write-Host "Veja um run de verdade acontecer ANTES de desligar o schedule: do publish.yml."
Write-Host "Runbook: docs/runbooks/producao-windows.md"
