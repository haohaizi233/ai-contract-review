$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$cache = Join-Path $root 'data\wecom_cli_sync'
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$log = Join-Path $cache 'task.log'
if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 5MB) {
    Move-Item -LiteralPath $log -Destination "$log.previous" -Force
}
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'tools\sync_wecom_cli.py') --trusted-source *>> $log
exit $LASTEXITCODE
