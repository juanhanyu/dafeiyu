@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

echo 正在让大肥鱼~ 开机自动启动...
> "%STARTUP%\DaFeiYuBot.vbs" echo CreateObject("WScript.Shell").Run "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""%~dp0run.ps1""", 0, False

echo 关掉正在运行的旧鱼鱼（如果有）...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'main.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -match 'run.ps1' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo 现在启动鱼鱼...
wscript "%STARTUP%\DaFeiYuBot.vbs"

echo.
echo 好啦！大肥鱼~ 已经在后台游起来了，以后开机也会自己启动。
echo 想关掉开机自启：删除 %STARTUP%\DaFeiYuBot.vbs
pause
