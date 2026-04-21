Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strCurrentPath = objFSO.GetParentFolderName(WScript.ScriptFullName)
strDesktop = objShell.SpecialFolders("Desktop")
strBatPath = strCurrentPath & "\run.bat"
strIconPath = strCurrentPath & "\assets\images\favicon.ico"

' Eliminar acceso directo anterior si existe
If objFSO.FileExists(strDesktop & "\G360 NC-Sustentor.lnk") Then
    objFSO.DeleteFile strDesktop & "\G360 NC-Sustentor.lnk", True
End If

Set objShortcut = objShell.CreateShortcut(strDesktop & "\G360 NC-Sustentor.lnk")
objShortcut.TargetPath = strBatPath
objShortcut.WorkingDirectory = strCurrentPath
objShortcut.Description = "G360 NC Sustentor - Sistema de Gestion"

' Aplicar icono personalizado SIEMPRE, incluso si no existe el archivo aun
If objFSO.FileExists(strIconPath) Then
    objShortcut.IconLocation = strIconPath & ", 0"
Else
    ' Fallback por si no esta el icono aun
    objShortcut.IconLocation = "%SystemRoot%\system32\shell32.dll, 15"
End If

objShortcut.Save

' Refrescar cache de iconos del escritorio
objShell.Run "ie4uinit.exe -show", 0, True
