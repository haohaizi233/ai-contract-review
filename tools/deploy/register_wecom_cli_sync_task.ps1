param([string]$TaskName = 'WeCom_CLI_RAG_Sync', [int]$IntervalMinutes = 5)
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$cache = Join-Path $root 'data\wecom_cli_sync'
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$old = Get-ScheduledTask -TaskName 'WeDrive_RAG_KnowledgeBase_Sync' -ErrorAction SilentlyContinue
if ($old) {
    $backup = Join-Path $cache 'previous-directory-sync-task.xml'
    if (-not (Test-Path -LiteralPath $backup)) {
        Export-ScheduledTask -TaskName $old.TaskName | Set-Content -LiteralPath $backup -Encoding Unicode
    }
    Stop-ScheduledTask -TaskName $old.TaskName
    Disable-ScheduledTask -TaskName $old.TaskName | Out-Null
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$runner = Join-Path $PSScriptRoot 'run_wecom_cli_sync.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$runner`"" -WorkingDirectory $root
$login = New-ScheduledTaskTrigger -AtLogOn -User $identity
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($login,$repeat) -Principal $principal -Settings $settings -Description 'Read-only WeCom CLI target-folder pull and incremental RAG indexing; authorization may require renewal.' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
