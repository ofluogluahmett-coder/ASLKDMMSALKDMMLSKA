@echo off
chcp 65001 > nul
cd /d %~dp0
title OTO TARAMA (sade toplayici)

REM Sade tarama botu: sadece ilan cekip oto_tarama.db'ye yazar.
REM MAX_TUR=0  -> sinirsiz (surekli calis)
REM MAX_TUR=10 -> 10 tur sonra temiz cikis
if "%MAX_TUR%"=="" set MAX_TUR=0
set PYTHONIOENCODING=utf-8

echo ============================================
echo   OTO TARAMA - sade toplayici
echo   Limit: MAX_TUR=%MAX_TUR%  (0 = sinirsiz)
echo ============================================
echo.
echo PX/CF ekrani gelirse AYRI pencerede: py -3.12 px_gec.py
echo.

py -3.12 -u oto_tarama.py

echo.
echo Bot durdu. Kapatmak icin bir tusa bas.
pause > nul
