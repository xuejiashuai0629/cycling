Option Explicit

Dim shell, fso, projectDir, i, pythonPath

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = projectDir

pythonPath = FindPython()

If IsSiteReady() Then
    OpenSite
    WScript.Quit
End If

shell.Run """" & pythonPath & """ -m streamlit run app.py --server.headless true", 0, False

For i = 1 To 120
    WScript.Sleep 250
    If IsSiteReady() Then
        Exit For
    End If
Next

If IsSiteReady() Then
    OpenSite
Else
    MsgBox "Website failed to start." & vbCrLf & "Python used: " & pythonPath & vbCrLf & "Please check Streamlit is installed.", 16, "Huawei PC Workspace"
End If

Function FindPython()
    Dim localAppData, candidates, path
    localAppData = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    candidates = Array( _
        localAppData & "\Python\pythoncore-3.14-64\python.exe", _
        localAppData & "\Python\pythoncore-3.15-64\python.exe", _
        localAppData & "\Programs\Python\Python314\python.exe", _
        localAppData & "\Programs\Python\Python315\python.exe", _
        "C:\Program Files\Python314\python.exe", _
        "C:\Program Files\Python315\python.exe", _
        "C:\Python314\python.exe", _
        "C:\Python315\python.exe" _
    )
    For Each path In candidates
        If fso.FileExists(path) Then
            FindPython = path
            Exit Function
        End If
    Next
    FindPython = "python"
End Function

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
