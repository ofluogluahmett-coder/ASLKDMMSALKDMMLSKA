# ══════════════════════════════════════════════════════════════════════
#  DURDUR — botu ve ONUN biraktigi tarayicilari temizler.
#
#  NEDEN: python surecini oldurmek yeterli DEGIL. uc'nin actigi tarayici
#  ve chromedriver YETIM kaliyor; her yeniden baslatmada birikiyorlar
#  (06.10.2026'da 22 yetim surec sayildi). RAM'i yiyor, port karisikligi
#  yapiyor ve hangi oturumun canli oldugunu belirsizlestiriyor.
#
#  KULLANICININ KENDI TARAYICISINA DOKUNULMAZ. Ayirt etme olcutu: bot
#  surecleri gecici profil (Temp\, scoped_dir) veya --remote-debugging-port
#  tasiyor; kullanicinin normal pencereleri tasimaz.
#
#  Kullanim:  powershell -ExecutionPolicy Bypass -File durdur.ps1
#         ya da:  durdur.bat
# ══════════════════════════════════════════════════════════════════════

$BOT_IZI = 'Temp\\|scoped_dir|--test-type|remote-debugging-port'

Write-Host "=== OTO TARAMA DURDURULUYOR ===" -ForegroundColor Cyan

# 1) Python surecleri (oto_tarama / oto_bot)
$py = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'" |
      Where-Object { $_.CommandLine -match 'oto_tarama|oto_bot' }
foreach ($p in $py) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host ("  python durduruldu: PID {0}" -f $p.ProcessId)
    } catch {}
}
if (-not $py) { Write-Host "  calisan bot yok" }

Start-Sleep -Milliseconds 800

# 2) chromedriver (her zaman bota ait)
$drv = Get-Process chromedriver, undetected_chromedriver -ErrorAction SilentlyContinue
foreach ($d in $drv) {
    try { Stop-Process -Id $d.Id -Force -ErrorAction Stop } catch {}
}
if ($drv) { Write-Host ("  chromedriver temizlendi: {0} surec" -f $drv.Count) }

# 3) BOTUN tarayicilari (kullanicinin pencereleri KORUNUR)
$tar = Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='brave.exe'"
$botTar = $tar | Where-Object { $_.CommandLine -match $BOT_IZI }
$kalan  = ($tar | Where-Object { -not ($_.CommandLine -match $BOT_IZI) }).Count
foreach ($t in $botTar) {
    try { Stop-Process -Id $t.ProcessId -Force -ErrorAction Stop } catch {}
}
Write-Host ("  bot tarayicilari kapatildi: {0} surec" -f $botTar.Count)
Write-Host ("  KULLANICININ tarayicisi korundu: {0} surec" -f $kalan) -ForegroundColor Green

Start-Sleep -Milliseconds 500
$son = (Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='brave.exe'" |
        Where-Object { $_.CommandLine -match $BOT_IZI }).Count
if ($son -gt 0) {
    Write-Host ("  [UYARI] {0} bot sureci hala ayakta" -f $son) -ForegroundColor Yellow
} else {
    Write-Host "  temiz." -ForegroundColor Green
}
