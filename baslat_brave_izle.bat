@echo off
chcp 65001 > nul
cd /d %~dp0

REM ══════════════════════════════════════════════════════════════════════
REM  KENDI PROFILINLE + DEBUG PORTUYLA BRAVE
REM
REM  Amac: botun degil SENIN oturumunu izlemek. Bu yuzden gecici profil
REM  DEGIL, kendi gunluk profilin kullaniliyor — cerezlerin, gecmisin,
REM  "guvenilir" oturumun oldugu gibi kalsin.
REM
REM  ONEMLI: Brave'in TAMAMEN kapali olmasi gerek. Acik bir Brave varsa
REM  bu komut yeni pencereyi ona devreder ve debug portu ACILMAZ.
REM  (Gorev yoneticisinden brave.exe kalmadigini dogrulayabilirsin.)
REM
REM  GIZLILIK: port acikken arac_izle.py sadece sahibinden isteklerinin
REM  URL/metot/durum/cache bilgisini ve cerez ISIMLERINI yazar; cerez
REM  degeri, form verisi, sayfa icerigi kaydedilmez. Isin bitince bu
REM  pencereyi kapat, port kapanir.
REM ══════════════════════════════════════════════════════════════════════

set PORT=9222
set BRAVE=C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe

tasklist /FI "IMAGENAME eq brave.exe" 2>nul | find /I "brave.exe" > nul
if %ERRORLEVEL% == 0 (
    echo.
    echo [DUR] Brave HALA ACIK. Once tamamen kapat, sonra bu dosyayi tekrar ac.
    echo       Acik Brave varsa debug portu acilmaz.
    echo.
    pause
    exit /b 1
)

echo ============================================
echo   BRAVE - kendi profilin + debug portu %PORT%
echo ============================================
echo.
echo 1) Acilan pencerede NORMAL gez:
echo      - sahibinden.com/otomobil?sorting=date_desc
echo      - sayfayi yenile, siralamayi degistir
echo      - bir ilana gir, geri don
echo.
echo 2) AYRI bir pencerede dinleyiciyi baslat:
echo      py -3.12 arac_izle.py
echo.
echo 3) Bitince dinleyiciyi Ctrl+C ile kapat, ozeti oku.
echo.

start "" "%BRAVE%" --remote-debugging-port=%PORT% --remote-allow-origins=* --no-first-run --no-default-browser-check "https://www.sahibinden.com/otomobil?sorting=date_desc"

echo Brave acildi (port %PORT%). Dinleyiciyi baslatabilirsin.
pause > nul
