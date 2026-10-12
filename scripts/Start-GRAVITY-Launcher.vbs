' 零視窗啟動 GRAVITY 開機腳本（供工作排程呼叫）
' project-root.txt 以 UTF-16 (Unicode) 儲存，含中文路徑；此處以 Unicode 讀取避免亂碼
Option Explicit
Dim sh, fso, marker, root, cmd, ts
Set sh = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
marker = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\GRAVITY\project-root.txt"
If Not fso.FileExists(marker) Then WScript.Quit 1
Set ts = fso.OpenTextFile(marker, 1, False, -1)
root = Trim(ts.ReadAll())
ts.Close
If Len(root) = 0 Then WScript.Quit 1
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\Start-GRAVITY-All.ps1"""
sh.Run cmd, 0, False
