' create_shortcut.vbs — Crea/renueva el acceso directo en el escritorio
' Target: run.bat (consola minimizada). Icono: assets\images\cipsa.ico
Option Explicit

Dim fso, shell, scriptDir, strDesktop, strTarget, strIcon, objShortcut
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
strDesktop = shell.SpecialFolders("Desktop")
strTarget = fso.BuildPath(scriptDir, "run.bat")
strIcon = fso.BuildPath(scriptDir, "assets\images\cipsa.ico")

If Not fso.FileExists(strTarget) Then
    MsgBox "No se encontro run.bat en:" & vbCrLf & scriptDir, vbCritical, "G360 NC Sustentor"
    WScript.Quit 1
End If

' Eliminar acceso directo anterior si existe
If fso.FileExists(strDesktop & "\G360 NC Sustentor.lnk") Then
    fso.DeleteFile strDesktop & "\G360 NC Sustentor.lnk", True
End If

Set objShortcut = shell.CreateShortcut(strDesktop & "\G360 NC Sustentor.lnk")
objShortcut.TargetPath = strTarget
objShortcut.WorkingDirectory = scriptDir
objShortcut.Description = "G360 NC Sustentor - Reconocimiento Comercial CIPSA"
objShortcut.WindowStyle = 7  ' 1=Normal, 3=Maximizada, 7=Minimizada

If fso.FileExists(strIcon) Then
    objShortcut.IconLocation = strIcon & ", 0"
Else
    objShortcut.IconLocation = "%SystemRoot%\system32\shell32.dll, 15"
End If

objShortcut.Save

' Refrescar cache de iconos del escritorio
shell.Run "ie4uinit.exe -show", 0, True

WScript.Echo "Acceso directo creado: G360 NC Sustentor.lnk"
