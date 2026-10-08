@echo off
chcp 65001 > nul
REM Otomobil toplayicisini ISINMA ile baslatir (soguk giris PX uretiyor).
REM Ayrinti: baslat_toplayici.ps1 basindaki not.
powershell -ExecutionPolicy Bypass -File "%~dp0baslat_toplayici.ps1"
pause
