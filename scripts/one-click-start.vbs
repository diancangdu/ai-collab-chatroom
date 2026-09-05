Option Explicit

Dim filesystem, shell, scriptDir, root, chatDir, pythonCmd
Dim configPath, samplePath, host, port, project, serverCommand, workloadCommand, url
Dim configText, configuredPython, value, file

Set filesystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = filesystem.GetParentFolderName(WScript.ScriptFullName)
root = filesystem.GetParentFolderName(scriptDir)
chatDir = root & "\chatroom"
configPath = root & "\config.json"
samplePath = root & "\config.example.json"

If Not filesystem.FileExists(configPath) And filesystem.FileExists(samplePath) Then
    filesystem.CopyFile samplePath, configPath
End If

host = "127.0.0.1"
port = "8787"
project = "main"
pythonCmd = "pythonw.exe"

If filesystem.FileExists(configPath) Then
    Set file = filesystem.OpenTextFile(configPath, 1, False, -1)
    configText = file.ReadAll
    file.Close
    Set file = Nothing

    value = ConfigString(configText, "python")
    If value <> "" Then configuredPython = value
    value = ConfigString(configText, "host")
    If value <> "" Then host = value
    value = ConfigNumber(configText, "port")
    If value <> "" Then port = value
End If

If WScript.Arguments.Named.Exists("host") Then host = WScript.Arguments.Named.Item("host")
If WScript.Arguments.Named.Exists("port") Then port = WScript.Arguments.Named.Item("port")
If WScript.Arguments.Named.Exists("project") Then project = WScript.Arguments.Named.Item("project")

If configuredPython <> "" Then
    If InStr(LCase(configuredPython), "python.exe") > 0 Then
        If InStr(LCase(configuredPython), "pythonw.exe") = 0 Then
            pythonCmd = Replace(configuredPython, "python.exe", "pythonw.exe")
        Else
            pythonCmd = configuredPython
        End If
    ElseIf InStr(LCase(configuredPython), "python") > 0 Then
        pythonCmd = "pythonw.exe"
    Else
        pythonCmd = configuredPython
    End If
End If

serverCommand = pythonCmd & " chatroom.py server --host " & host & " --port " & port
workloadCommand = pythonCmd & " workload.py watch --project " & project & " --host " & host & " --port " & port
url = "http://" & host & ":" & port & "/?project=" & project

shell.CurrentDirectory = chatDir
shell.Run serverCommand, 0, False
WScript.Sleep 1500
shell.Run workloadCommand, 0, False
WScript.Sleep 1500
If Not WScript.Arguments.Named.Exists("nobrowser") Then
    shell.Run url, 1, False
End If

Function ConfigString(text, key)
    Dim regex, matches
    Set regex = New RegExp
    regex.Global = False
    regex.IgnoreCase = True
    regex.Pattern = """" & key & """\s*:\s*""([^""]*)"""
    Set matches = regex.Execute(text)
    If matches.Count > 0 Then
        ConfigString = matches.Item(0).SubMatches.Item(0)
    Else
        ConfigString = ""
    End If
End Function

Function ConfigNumber(text, key)
    Dim regex, matches
    Set regex = New RegExp
    regex.Global = False
    regex.IgnoreCase = True
    regex.Pattern = """" & key & """\s*:\s*([0-9]+)"
    Set matches = regex.Execute(text)
    If matches.Count > 0 Then
        ConfigNumber = matches.Item(0).SubMatches.Item(0)
    Else
        ConfigNumber = ""
    End If
End Function
