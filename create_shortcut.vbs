Set objShell = CreateObject("WScript.Shell")
strDesktop = objShell.SpecialFolders("Desktop")
strBatPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\run.bat"
strIconPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\assets\images\favicon.ico"

Set objShortcut = objShell.CreateShortcut(strDesktop & "\G360 NC-Sustentor.lnk")
objShortcut.TargetPath = strBatPath
objShortcut.WorkingDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(strBatPath)
objShortcut.IconLocation = strIconPath
objShortcut.Save
