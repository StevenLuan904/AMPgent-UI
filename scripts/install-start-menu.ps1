$ErrorActionPreference = 'Stop'
$appRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$launcherPath = Join-Path $PSScriptRoot 'start-ampgent.ps1'
$iconPath = Join-Path $appRoot 'public\ampgent.ico'
$startMenuDirectory = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$shortcutPath = Join-Path $startMenuDirectory 'AMPgent 科学分析.lnk'
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $iconPath)) {
    throw "未找到应用图标：$iconPath"
}
if (-not (Test-Path -LiteralPath $powerShellPath)) {
    throw "未找到 Windows PowerShell：$powerShellPath"
}

New-Item -ItemType Directory -Path $startMenuDirectory -Force | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powerShellPath
$shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`""
$shortcut.WorkingDirectory = $appRoot
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = '启动 AMPgent 短肽科学分析工作台'
$shortcut.Save()

Write-Host "开始菜单入口已安装：$shortcutPath" -ForegroundColor Green
