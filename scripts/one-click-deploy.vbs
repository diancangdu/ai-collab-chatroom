Option Explicit

Dim filesystem, shell, scriptPath, scriptDir, root, chatDir, configPath, samplePath
Dim host, port, project, pythonCmd, configuredPython, configText, value, file
Dim desktopPath, shortcutPath, shortcut, tempFile, versionText, url

Set filesystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptPath = WScript.ScriptFullName
scriptDir = filesystem.GetParentFolderName(scriptPath)
root = filesystem.GetParentFolderName(scriptDir)
chatDir = filesystem.BuildPath(root, "chatroom")
configPath = filesystem.BuildPath(root, "config.json")
samplePath = filesystem.BuildPath(root, "config.example.json")
tempFile = filesystem.BuildPath(shell.ExpandEnvironmentStrings("%TEMP%"), "aicollab-python-version.txt")

host = "127.0.0.1"
port = "8787"
project = "allagentstudy"
pythonCmd = "pythonw.exe"
configuredPython = ""

If Not filesystem.FileExists(configPath) And filesystem.FileExists(samplePath) Then
    filesystem.CopyFile samplePath, configPath
End If

If filesystem.FileExists(configPath) Then
    Set file = filesystem.OpenTextFile(configPath, 1, False, -1)
    configText = file.ReadAll
    file.Close
    value = ConfigString(configText, "python")
    If value <> "" Then configuredPython = value
    value = ConfigString(configText, "host")
    If value <> "" Then host = value
    value = ConfigNumber(configText, "port")
    If value <> "" Then port = value
    value = ConfigString(configText, "project")
    If value <> "" Then project = value
End If

If WScript.Arguments.Named.Exists("host") Then host = WScript.Arguments.Named.Item("host")
If WScript.Arguments.Named.Exists("port") Then port = WScript.Arguments.Named.Item("port")
If WScript.Arguments.Named.Exists("project") Then project = WScript.Arguments.Named.Item("project")

If configuredPython <> "" Then
    If InStr(LCase(configuredPython), "python.exe") > 0 And InStr(LCase(configuredPython), "pythonw.exe") = 0 Then
        pythonCmd = Replace(configuredPython, "python.exe", "pythonw.exe")
    ElseIf InStr(LCase(configuredPython), "python.exe") > 0 Then
        pythonCmd = configuredPython
    Else
        pythonCmd = configuredPython
    End If
End If

shell.Run "%COMSPEC% /c python --version > """ & tempFile & """ 2>&1", 0, True
If filesystem.FileExists(tempFile) Then
    Set file = filesystem.OpenTextFile(tempFile, 1, False, -1)
    versionText = file.ReadAll
    file.Close
    If InStr(versionText, "Python 3.") = 0 Then
        MsgBox "Python 3.9+ is required. Found: " & versionText, 16, "AI Collab Chatroom"
        WScript.Quit 2
    End If
Else
    MsgBox "Unable to verify Python. Install Python 3.9+ and try again.", 16, "AI Collab Chatroom"
    WScript.Quit 2
End If

shell.CurrentDirectory = chatDir
shell.Run pythonCmd & " chatroom.py server --host " & host & " --port " & port, 0, False
WScript.Sleep 1500
shell.Run pythonCmd & " workload.py watch --project " & project & " --host " & host & " --port " & port, 0, False
WScript.Sleep 1500

desktopPath = shell.SpecialFolders("Desktop")
shortcutPath = filesystem.BuildPath(desktopPath, "AI Collab Chatroom.lnk")
Set shortcut = shell.CreateShortcut(shortcutPath)
shortcut.TargetPath = scriptPath
shortcut.Arguments = "/project:""" & project & """"
shortcut.WorkingDirectory = root
shortcut.Description = "Start the AI Collab Chatroom desktop UI"
shortcut.Save

url = "http://" & host & ":" & port & "/desktop?project=" & project
shell.Run url, 1, False

MsgBox "Deployment ready." & vbCrLf & vbCrLf & "Desktop shortcut: " & shortcutPath & vbCrLf & "Desktop UI: " & url, 64, "AI Collab Chatroom"

Function ConfigString(text, key)
    Dim regex, matches
    Set regex = New RegExp
    regex.Global = False
    regex.IgnoreCase = True
    regex.Pattern = """" & key & """\s*:\s*""([^""]*)"""
    Set matches = regex.Execute(text)
    If matches.Count > 0 Then ConfigString = matches.Item(0).SubMatches.Item(0)
End Function

Function ConfigNumber(text, key)
    Dim regex, matches
    Set regex = New RegExp
    regex.Global = False
    regex.IgnoreCase = True
    regex.Pattern = """" & key & """\s*:\s*([0-9]+)"
    Set matches = regex.Execute(text)
    If matches.Count > 0 Then ConfigNumber = matches.Item(0).SubMatches.Item(0)
End Function
