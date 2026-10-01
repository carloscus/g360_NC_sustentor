' launch.vbs — Lanza G360 NC Sustentor (consola minimizada; restaura para ver errores)
' Busca run.bat junto a este script; si no existe avisa con dialogo grafico.
Option Explicit

Dim fso, shell, scriptDir, target, logFile
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
target = fso.BuildPath(scriptDir, "run.bat")

' El log de la app va a APPDATA (evita PermissionError con el .bat)
Dim appDataDir
appDataDir = shell.SpecialFolders("AppData")
logFile = fso.BuildPath(fso.BuildPath(appDataDir, "g360-erp-nc-sustentor"), "app.log")

If Not fso.FileExists(target) Then
    MsgBox "No se encontro run.bat en:" & vbCrLf & scriptDir & vbCrLf & vbCrLf & _
           "Asegurate de copiar toda la carpeta del proyecto.", _
           vbCritical, "G360 NC Sustentor"
    WScript.Quit 1
End If

' 7 = minimizada: el arranque es silencioso, pero la consola queda disponible
' para diagnosticar si algo falla (uv sync, permisos, etc).
' False = no esperar a que termine (asincronico).
' Se pasa "fast": run.bat salta uv/Python/venv/sync/migracion/acceso-directo
' si el entorno ya existe (inicio rapido de uso diario).
shell.CurrentDirectory = scriptDir
shell.Run Chr(34) & target & Chr(34) & " fast", 7, False

' Si hay error de arranque, mostrar log en dialog
On Error Resume Next
WScript.Sleep 8000
If fso.FileExists(logFile) Then
    Dim lastLine, allText, lines, i, f
    On Error Resume Next
    Set f = fso.OpenTextFile(logFile, 1, False)
    allText = f.ReadAll
    f.Close
    lines = Split(allText, vbNewLine)
    For i = UBound(lines) To UBound(lines) - 10 Step -1
        If Len(Trim(lines(i))) > 0 Then
            lastLine = lines(i)
            Exit For
        End If
    Next
    If InStr(lastLine, "ERROR") > 0 Or InStr(lastLine, "Error") > 0 Or InStr(lastLine, "FATAL") > 0 Then
        MsgBox "Error detectado en el arranque:" & vbCrLf & vbCrLf & _
               Left(lastLine, 250) & vbCrLf & vbCrLf & _
               "Log completo: " & logFile, _
               vbExclamation, "G360 NC Sustentor"
    End If
End If
