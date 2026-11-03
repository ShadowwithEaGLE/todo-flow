@echo off
setlocal
cd /d "%~dp0"

REM shortcut: assumes the wireless adapter is named WLAN; change the alias if this PC uses another name.
for /f "delims=" %%I in ('powershell.exe -NoProfile -Command "(Get-NetIPAddress -InterfaceAlias WLAN -AddressFamily IPv4 -AddressState Preferred).IPAddress"') do if not defined LAN_IP set "LAN_IP=%%I"

if not defined LAN_IP (
  echo Could not find a usable IPv4 address on adapter WLAN.
  echo Check the adapter name in this script.
  pause
  exit /b 1
)
if "%LAN_IP:~0,8%"=="169.254." (
  echo Adapter WLAN has no usable LAN address.
  pause
  exit /b 1
)

echo Flow Board URL: http://%LAN_IP%:8080/todo-flow.html
echo Keep this window open while the board is in use. Press Ctrl+C to stop.
echo.
py -3 server.py --host "%LAN_IP%" --port 8080
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Flow Board stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
