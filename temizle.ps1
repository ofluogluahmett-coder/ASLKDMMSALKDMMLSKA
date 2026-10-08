# ══════════════════════════════════════════════════════════════════════
#  TEMIZLE — SADECE YETIM tarayici/driver sureclerini toplar.
#  Canli bot CALISMAYA DEVAM EDER, kullanicinin tarayicisina DOKUNULMAZ.
#
#  NEDEN: python surecini oldurmek tarayiciyi oldurmuyor; uc'nin actigi
#  tarayici ve chromedriver yetim kaliyor ve her yeniden baslatmada
#  birikiyor (06.10.2026: 22 yetim surec, 9'u gercekten sahipsizdi).
#
#  Ayirt etme: "bot izi" = gecici profil (Temp\, scoped_dir) veya
#  --test-type / --remote-debugging-port. Yetim = bot izi tasiyan ama
#  CANLI bot python surecinin soy agacinda OLMAYAN surec.
#
#  Kullanim: powershell -ExecutionPolicy Bypass -File temizle.ps1
# ══════════════════════════════════════════════════════════════════════

$BOT_IZI = 'Temp\\|scoped_dir|--test-type|remote-debugging-port'
$tum = Get-CimInstance Win32_Process

# ⚠ 08.10.2026 — PC BILESENI BOTU KORUMASI.
# apex_predator'un PC botu da undetected_chromedriver kullaniyor ve
# anonim modda gecici profille calisiyor, yani yukaridaki "bot izi"
# desenine GIRIYOR. Ikisi birden kosarken bu betik onun tarayicisini
# yetim sanip oldururdu. Bu yuzden PC botu calisiyorken temizlik
# yapilmiyor: hangi yetimin kime ait oldugu guvenle ayirt edilemez.
$pcBot = $tum | Where-Object {
    ($_.Name -eq 'python.exe' -or $_.Name -eq 'py.exe') -and
    $_.CommandLine -match 'sahibinden_bot|letgo_bot|kelepir_avci|analiz_botu'
}
if ($pcBot) {
    $n = ($pcBot | Measure-Object).Count
    Write-Host "PC bileseni botu CALISIYOR ($n surec) - temizlik ATLANDI." -ForegroundColor Yellow
    Write-Host "Yetim surec birikmisse PC botunu durdurup tekrar calistir."
    exit 0
}

$canli = ($tum | Where-Object {
    ($_.Name -eq 'python.exe' -or $_.Name -eq 'py.exe') -and
    $_.CommandLine -match 'oto_tarama|oto_bot'
}).ProcessId

function AtaZinciri($hedefPid) {
    $zincir = @()
    $p = $tum | Where-Object ProcessId -eq $hedefPid
    $k = 0
    while ($p -and $k -lt 12) {
        $zincir += $p.ParentProcessId
        $p = $tum | Where-Object ProcessId -eq $p.ParentProcessId
        $k++
    }
    return $zincir
}

Write-Host "=== YETIM SUREC TEMIZLIGI ===" -ForegroundColor Cyan
if ($canli) { Write-Host ("  canli bot PID: {0}" -f ($canli -join ', ')) }
else { Write-Host "  calisan bot yok -> bot izi tasiyan her sey yetim sayilir" }

$botTar = $tum | Where-Object {
    ($_.Name -eq 'chrome.exe' -or $_.Name -eq 'brave.exe') -and
    $_.CommandLine -match $BOT_IZI
}

$yetim = @()
foreach ($t in $botTar) {
    $zincir = AtaZinciri $t.ProcessId
    $aitMi = $false
    if ($canli) { foreach ($c in $canli) { if ($zincir -contains $c) { $aitMi = $true } } }
    if (-not $aitMi) { $yetim += $t }
}

foreach ($y in $yetim) { try { Stop-Process -Id $y.ProcessId -Force -ErrorAction Stop } catch {} }
Write-Host ("  yetim tarayici kapatildi: {0}" -f $yetim.Count)

# Yetim chromedriver'lar
$drvYetim = $tum | Where-Object {
    ($_.Name -eq 'chromedriver.exe' -or $_.Name -eq 'undetected_chromedriver.exe')
} | Where-Object {
    $zincir = AtaZinciri $_.ProcessId
    $aitMi = $false
    if ($canli) { foreach ($c in $canli) { if ($zincir -contains $c) { $aitMi = $true } } }
    -not $aitMi
}
foreach ($d in $drvYetim) { try { Stop-Process -Id $d.ProcessId -Force -ErrorAction Stop } catch {} }
Write-Host ("  yetim chromedriver kapatildi: {0}" -f ($drvYetim | Measure-Object).Count)

Start-Sleep -Milliseconds 600
$kalanKul = ($tum = Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='brave.exe'" |
             Where-Object { -not ($_.CommandLine -match $BOT_IZI) } | Measure-Object).Count
Write-Host ("  kullanicinin tarayicisi korundu: {0} surec" -f $kalanKul) -ForegroundColor Green
