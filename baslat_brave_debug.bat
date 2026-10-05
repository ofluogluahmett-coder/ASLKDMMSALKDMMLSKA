@echo off
chcp 65001 > nul
cd /d %~dp0

REM ══════════════════════════════════════════════════════════════════════
REM  ELLE ISITILAN BRAVE  (attach modu icin)
REM
REM  05.10.2026 bulgusu: elle kullanilan Brave ayni IP'de /otomobil'de
REM  serbest geziyor; botun kendi actigi soguk oturum tek istekte blok
REM  yiyor. Bu yuzden bot YENI tarayici acmak yerine ELLE isitilmis,
REM  cerezi oturmus bir tarayiciya baglanir.
REM
REM  NOT: Kendi gunluk profilin KULLANILMIYOR — ayri bir profil
REM  (brave_insan_profile) aciliyor. Boylece kendi hesaplarin/cerezlerin
REM  bota aciliyor olmuyor; sen o pencerede normal gezerek profili
REM  "insan" haline getiriyorsun.
REM ══════════════════════════════════════════════════════════════════════

set PORT=9222
set BRAVE=C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe
set PROFIL=%~dp0brave_insan_profile

echo ============================================
echo   ELLE ISITILAN BRAVE - port %PORT%
echo ============================================
echo.
echo 1) Acilan pencerede 1-2 dakika NORMAL gez:
echo      ana sayfa -^> Vasita -^> Otomobil -^> bir ilana gir -^> geri don
echo    (CF "basili tut" cikarsa BEKLE, tiklamadan gecer)
echo.
echo 2) Sonra AYRI bir pencerede botu attach modunda baslat:
echo      set CDP_PORT=%PORT%
echo      py -3.12 -u oto_tarama.py
echo.
echo 3) Bu pencereyi KAPATMA, Brave'i de kapatma.
echo.

start "" "%BRAVE%" --remote-debugging-port=%PORT% --remote-allow-origins=* --user-data-dir="%PROFIL%" --no-first-run --no-default-browser-check "https://www.sahibinden.com/"

echo Brave acildi. Gezinmeyi bitirince 2. adima gec.
pause > nul
