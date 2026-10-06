@echo off
chcp 65001 > nul
cd /d %~dp0
REM Botu ve ONUN tarayicilarini kapatir. Kullanicinin kendi tarayicisina
REM DOKUNMAZ. Yeniden baslatmadan ONCE bunu calistir — yoksa yetim
REM tarayicilar birikiyor (06.10.2026: 22 surec birikmisti).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0durdur.ps1"
pause
