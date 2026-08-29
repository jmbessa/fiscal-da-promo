' Executa um comando SEM abrir janela de terminal.
'
' Por que este arquivo existe (2026-08-28): o afiliado.exe e aplicacao de
' console, e o Agendador de Tarefas do Windows, no modo Interactive, abre uma
' janela na tela do dono a cada 15 minutos. O modo S4U ("executar estando o
' usuario conectado ou nao") roda sem janela, mas registrar tarefa S4U exige
' ELEVACAO -- medido nesta maquina: Register-ScheduledTask devolveu
' "Acesso negado" (HRESULT 0x80070005). Entao a tarefa segue Interactive e
' quem esconde a janela e este atalho.
'
' O 0 do Run e o WindowStyle: janela oculta.
' O True e "espere terminar", e ele NAO e opcional: com False o wscript sai na
' hora, o Agendador da a tarefa por concluida e os freios que dependem da
' duracao -- MultipleInstances IgnoreNew (um run travado nao empilha dez) e
' ExecutionTimeLimit -- deixariam de valer em silencio.
'
' O codigo de saida do processo e devolvido ao Agendador, para o
' LastTaskResult continuar dizendo a verdade.

Option Explicit

Dim sh, i, arg, cmd, codigo
Set sh = CreateObject("WScript.Shell")

If WScript.Arguments.Count = 0 Then
    WScript.Echo "uso: afiliado-oculto.vbs <programa> [argumentos...]"
    WScript.Quit 2
End If

cmd = ""
For i = 0 To WScript.Arguments.Count - 1
    arg = WScript.Arguments(i)
    ' Caminho com espaco precisa de aspas; argumento simples nao pode ganhar
    ' aspas, senao o argparse do Python recebe o par de aspas junto.
    If InStr(arg, " ") > 0 Then arg = """" & arg & """"
    If i > 0 Then cmd = cmd & " "
    cmd = cmd & arg
Next

codigo = sh.Run(cmd, 0, True)
WScript.Quit codigo
