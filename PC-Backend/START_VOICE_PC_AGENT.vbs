Set shell = CreateObject("WScript.Shell")
cmd = Chr(34) & "C:\Python314\python.exe" & Chr(34) & " " & Chr(34) & "E:\00 - Master Tools & Switch Board\Control-Center & Switch-Board\Voice-PC-Agent\PC-Backend\start_server.pyw" & Chr(34)
shell.Run cmd, 0, False
