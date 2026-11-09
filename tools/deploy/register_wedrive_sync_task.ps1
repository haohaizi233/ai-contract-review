param(
    [switch]$EnableProduction = $false,
    [string]$TaskName = "WeDrive_RAG_KnowledgeBase_Sync",
    [string]$PythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe",
    [string]$ScriptPath = "C:\Users\Administrator\.gemini\antigravity\worktrees\AI合同\check_weipan_permissions\tools\sync_wecom_wedrive.py",
    [string]$WatchDir = "D:\Users\Administrator\Documents\WXWork\1688855905806482\WeDrive\恒程创新\法律事务部（含商务合同等文件）",
    [int]$IntervalSeconds = 60
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "企业微信微盘 RAG 增量同步守护进程 - 任务计划程序部署工具" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

if (-not $EnableProduction) {
    Write-Host "`n[安全拦截] 当前处于 STAGING 预备模式，未传递 -EnableProduction 参数。" -ForegroundColor Yellow
    Write-Host "生产任务尚未在系统注册。若已通过真实微盘同步实验并确认启用，请执行：" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File tools/deploy/register_wedrive_sync_task.ps1 -EnableProduction`n" -ForegroundColor Green
    
    Write-Host "【拟定任务计划配置参数预览】" -ForegroundColor Cyan
    Write-Host "  任务名称       : $TaskName"
    Write-Host "  监控目录       : $WatchDir"
    Write-Host "  轮询间隔       : $IntervalSeconds 秒 (5分钟)"
    Write-Host "  执行命令       : $PythonExe `"$ScriptPath`" --local-dir `"$WatchDir`" --daemon --interval $IntervalSeconds --sync-deletion"
    Write-Host "  触发条件       : 用户登录时启动 (AtLogon)"
    Write-Host "  故障恢复       : 崩溃后 1 分钟自动重启，最多重试 3 次"
    Write-Host "  进程互斥保证   : 基于 staging/wedrive_sync.pid 进程锁，杜绝双写冲突"
    exit 0
}

Write-Host "`n[授权确认] 正在向 Windows 任务计划程序注册后台常驻同步任务..." -ForegroundColor Green

if (-not (Test-Path $ScriptPath)) {
    Write-Error "找不到目标 Python 脚本: $ScriptPath"
    exit 1
}

$Arguments = "`"$ScriptPath`" --local-dir `"$WatchDir`" --daemon --interval $IntervalSeconds --sync-deletion"
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $Arguments -WorkingDirectory (Split-Path $ScriptPath -Parent)
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 365)

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "企业微信微盘法律部合同增量 RAG 知识库安全同步守护进程"
    Write-Host "任务计划程序注册成功: $TaskName" -ForegroundColor Green
} catch {
    Write-Error "注册任务计划程序失败: $_"
    exit 1
}
