$ErrorActionPreference = 'Stop'
$shortcutPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\AMPgent 科学分析.lnk'

if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
    Write-Host "开始菜单入口已移除：$shortcutPath" -ForegroundColor Green
}
else {
    Write-Host '开始菜单中没有 AMPgent 入口。'
}
