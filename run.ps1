# 大肥鱼~ 守护脚本：开机自动启动，挂了自动重启。由「启动」文件夹里的 DaFeiYuBot.vbs 调用。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $root ".venv\Scripts\python.exe"
$log = Join-Path $root "bot.log"

function Write-Log($msg) { Add-Content -Path $log -Value ("{0} [run] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg) -Encoding utf8 }

# 等文件就位（比如刚搬家时）
if (-not (Test-Path (Join-Path $root ".env"))) {
    while (-not (Test-Path (Join-Path $root ".env"))) { Start-Sleep 10 }
    Start-Sleep 60  # 等复制完成
}

# 虚拟环境不存在或坏了就重建
& $py -c "import botpy, openai, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Log "rebuilding .venv"
    if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
    python -m venv .venv
    & $py -m pip install -q -r requirements.txt pillow *>> $log
}

while ($true) {
    Write-Log "starting bot"
    & $py main.py *>> $log
    Write-Log "bot exited ($LASTEXITCODE), restarting in 15s"
    Start-Sleep 15
    # 日志太大就截断
    if ((Get-Item $log).Length -gt 20MB) { Get-Content $log -Tail 2000 | Set-Content "$log.tmp" -Encoding utf8; Move-Item -Force "$log.tmp" $log }
}
