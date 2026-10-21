Option Explicit

Dim shell, fso, projectDir, i, ready

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = projectDir

If IsSiteReady() Then
    OpenSite
    WScript.Quit
End If

shell.Run "python -m streamlit run app.py --server.headless true", 0, False

For i = 1 To 120
    WScript.Sleep 250
    If IsSiteReady() Then
        Exit For
    End If
Next

If IsSiteReady() Then
    OpenSite
Else
    MsgBox "Website failed to start. Please check Python / Streamlit.", 16, "Huawei PC Workspace"
End If

Function IsSiteReady()
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    http.setTimeouts 500, 500, 500, 500
    http.Open "GET", "http://127.0.0.1:8501", False
    http.Send
    IsSiteReady = (http.Status >= 200 And http.Status < 500)
    On Error GoTo 0
End Function

Sub OpenSite()
    shell.Run "http://127.0.0.1:8501", 0, False
End Sub
