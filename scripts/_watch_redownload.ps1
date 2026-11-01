# 监视附件重下任务：终端输出出现"====="汇总即退出并打印尾部
$log = 'C:\Users\Administrator\.cursor\projects\d-Desktop-AI\terminals\310884.txt'
while ($true) {
  if (Test-Path $log) {
    $c = Get-Content $log -Raw
    if ($c -match '===== ') {
      Write-Output 'JOB_FINISHED_SUMMARY'
      Write-Output $c.Substring([Math]::Max(0, $c.Length - 900))
      break
    }
  }
  Start-Sleep -Seconds 30
}
