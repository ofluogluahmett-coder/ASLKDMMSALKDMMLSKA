@echo off
chcp 65001 > nul
cd /d %~dp0

REM ══════════════════════════════════════════════════════════════════════
REM  21.09.2026 — LOG PENCERE BIRIKMESI DUZELTMESI
REM  Sorun: Her calistirmada yeni bir cmd penceresi aciliyor, eskisi
REM  kapatilmiyordu (oto_bot_run2.log ... run13.log birikmisti).
REM  Cozum: Bu pencere TEK pencere olarak kalir. Bot cikinca pencere
REM  KAPANMAZ ama yeni pencere de ACILMAZ — ayni pencerede yeniden
REM  baslatilir. Log dosyasi da SABIT isim (oto.log) kullanir; eski
REM  run*.log dosyalari bir daha uretilmez.
REM ══════════════════════════════════════════════════════════════════════

REM Eski birikmis run log dosyalarini temizle (tek seferlik temizlik).
if exist oto_bot_run*.log del /q oto_bot_run*.log > nul 2>&1

title OTO KELEPIR AVCISI - Calisiyor (bu pencereyi KAPATMA)

:LOOP
cls
echo ============================================
echo   OTO KELEPIR AVCISI - Faz 1 Toplayici
echo ============================================
echo.
echo Brave gorunur acilacak. CF/PX cikarsa ELLE gec.
echo Gecince pencere ekran disina alinir, tarama baslar.
echo.
echo CANLI LOG: Asagida botun tum adimlari ANLIK akar (bu pencereyi
echo kapatma). Ayni log oto.log dosyasina da yazilir.
echo.
echo (Bot durursa bu pencere ACIK kalir; kapatmadan yeniden baslatilir.)
echo.

REM 21.09.2026 — CANLI LOG: py ciktiyi DOGRUDAN bu pencereye basar
REM (yonlendirme YOK). StreamHandler sayesinde her satir aninda gorunur.
py -3.12 oto_bot.py

echo.
echo ============================================
echo   BOT DURDU. Yeniden baslatmak icin bir tusa bas.
echo   (Pencereyi kapatmak icin Ctrl+C)
echo ============================================
pause > nul
goto LOOP
