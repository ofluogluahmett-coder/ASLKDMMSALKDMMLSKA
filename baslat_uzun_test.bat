@echo off
chcp 65001 > nul
cd /d %~dp0
title OTO TARAMA - GUN BOYU TEST (bu pencereyi kapatma)

REM ══════════════════════════════════════════════════════════════════════
REM  GUN BOYU GOZETIMSIZ TEST (07.10.2026)
REM
REM  Amac: yogunluk saatlerinde (is saatleri) bot kendi basina ne kadar
REM  dayaniyor, ne kadar ilan topluyor, ilan kaciriyor mu?
REM
REM  Konfigurasyon = LOG'DAN DOGRULANMIS temiz kosu (05.10, 31 tur / 0 PX)
REM  + tek ekleme olarak 50'lik liste:
REM    Brave, anonim gecici profil, isinmali giris (ana sayfa -> kategori)
REM    gorsel engeli YOK, cache-bust YOK, cache-disabled YOK
REM    tempo 50-75 sn (tur TOPLAM suresi sabit tutulur)
REM    sayfa boyu 50 (sitenin kendi dugmesine tiklanarak)
REM    derin sayfa (pagingOffset) YOK — bosluk SADECE OLCULUR
REM  Gun boyu kosu icin TEK fark: HAVUZ=1 (PX'te diger tarayiciya atla,
REM  ust uste blokta ping-pong freni devreye girer).
REM
REM  Durdurmak icin: durdur.bat  (yetim tarayici birakmaz)
REM  Rapor icin    : py -3.12 arac_px_rapor.py px_gun.log
REM ══════════════════════════════════════════════════════════════════════

REM Once eski/yetim surecleri temizle
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0durdur.ps1"

set PYTHONIOENCODING=utf-8
set MAX_TUR=0
set GUVENLI_MOD=1
set TARAYICI=brave
set SAYFA_BOYU=50
REM 07.10.2026 — SAYFA BOYU TIKLAMA ILE DEGIL URL ILE. Olcum: PX tam
REM "50"ye TIKLADIGI ANDA geldi (iki ayri oturumda, ayni noktada;
REM kullanici ikisini de elle gecmek zorunda kaldi). Arada pagingSize=50
REM URL'iyle 15 tur sorunsuz dondu. Yani suclu parametre degil, GECIS
REM HAREKETI. Dogrudan URL ile giris denendi: acilista PX GELMEDI.
set SAYFA_BOYU_MOD=url
set PARTI_DUYARLI=0
set HAVUZ=1
set TEMEL_MIN=50
set TEMEL_MAX=75

echo ============================================
echo   GUN BOYU TEST
echo ============================================
echo   tarayici    : Brave (PX'te Chrome'a atlar)
echo   tempo       : 50-75 sn
echo   sayfa boyu  : 50  (DOGRUDAN URL ile, tiklama YOK)
echo   derin sayfa : YOK (bosluk sadece olculur)
echo.
echo   Log: px_gun.log   (canli izlemek icin baska pencerede:
echo        powershell "Get-Content px_gun.log -Wait -Tail 20")
echo.
echo   Elle mudahale gerekirse ELLE_MUDAHALE_GEREKLI.txt olusur.
echo.

py -3.12 -u oto_tarama.py > px_gun.log 2>&1

echo.
echo ============================================
echo   BOT DURDU. Rapor:
echo     py -3.12 arac_px_rapor.py px_gun.log
echo ============================================
pause
