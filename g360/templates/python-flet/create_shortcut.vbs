Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strCurrentPath = objFSO.GetParentFolderName(WScript.ScriptFullName)
strDesktop = objShell.SpecialFolders("Desktop")

strExePath = strCurrentPath & "\dist\G360App-Portable.exe"
strBatPath = strCurrentPath & "\run.bat"
strIconPath = strCurrentPath & "\assets\images\app.ico"

targetPath = strExePath
if not objFSO.FileExists(strExePath) Then
    targetPath = strBatPath
End If

appName = "G360 App"

If objFSO.FileExists(strDesktop & "\" & appName & ".lnk") Then
    objFSO.DeleteFile strDesktop & "\" & appName & ".lnk", True
End If

Set objShortcut = objShell.CreateShortcut(strDesktop & "\" & appName & ".lnk")
objShortcut.TargetPath = targetPath
objShortcut.WorkingDirectory = strCurrentPath
objShortcut.Description = "G360 Desktop Application"

If objFSO.FileExists(strIconPath) Then
    objShortcut.IconLocation = strIconPath & ", 0"
Else
    objShortcut.IconLocation = "%SystemRoot%\system32\shell32.dll, 15"
End If

objShortcut.Save

objShell.Run "ie4uinit.exe -show", 0, True