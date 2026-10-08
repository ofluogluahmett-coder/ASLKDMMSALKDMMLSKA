# ══════════════════════════════════════════════════════════════════════
#  DURDUR — SADECE OTO botunu ve ONUN biraktigi tarayicilari temizler.
#
#  NEDEN: python surecini oldurmek yeterli DEGIL. uc'nin actigi tarayici
#  ve chromedriver YETIM kaliyor; her yeniden baslatmada birikiyorlar
#  (06.10.2026'da 22 yetim surec sayildi). RAM'i yiyor, port karisikligi
#  yapiyor ve hangi oturumun canli oldugunu belirsizlestiriyor.
#
#  ⚠ 08.10.2026 — KRITIK DUZELTME (iki bot birden kosacagi icin):
#  Eski surum TUM chromedriver'lari ve komut satirinda "Temp\ /
#  scoped_dir / --test-type / remote-debugging-port" gecen TUM
#  tarayicilari olduruyordu. Ama apex_predator'un PC BILESENLERI botu da
#  undetected_chromedriver kullaniyor ve anonim modda tam olarak gecici
#  profille calisiyor — yani ikisi birden kosarken bu betik PC BOTUNU
#  OLDURUYORDU.
#
#  YENI OLCUT: hedef = OTO python surecinin SOYUNDAN gelen surecler.
#  Soyagaci (python -> chromedriver -> brave) yurunur; baskasinin
#  surecine dokunulmaz. Gercekten yetim kalanlar (ebeveyni olmus) icin
#  marker taramasi yapilir, ama PC botu calisiyorsa o tarama ATLANIR.
#
#  KULLANICININ KENDI TARAYICISINA ASLA DOKUNULMAZ.
#  Uzanti toplayicisi ve yerel sunucu da bu betikten ETKILENMEZ.
#
#  Kullanim:  powershell -ExecutionPolicy Bypass -File durdur.ps1
#         ya da:  durdur.bat
# ══════════════════════════════════════════════════════════════════════

$BOT_IZI = 'Temp\\|scoped_dir|--test-type|remote-debugging-port'

Write-Host "=== OTO TARAMA DURDURULUYOR ===" -ForegroundColor Cyan

$tumSurec = Get-CimInstance Win32_Process

# PC bileseni botu calisiyor mu? (apex_predator)
$pcBot = $tumSurec | Where-Object {
    ($_.Name -eq 'python.exe' -or $_.Name -eq 'py.exe') -and
    $_.CommandLine -match 'sahibinden_bot|letgo_bot|kelepir_avci|analiz_botu'
}
if ($pcBot) {
    $n = ($pcBot | Measure-Object).Count
    Write-Host ("  ! PC bileseni botu CALISIYOR ($n surec) - ona ait surecler korunacak") -ForegroundColor Yellow
}

# 1) OTO python surecleri
$py = $tumSurec | Where-Object {
    ($_.Name -eq 'python.exe' -or $_.Name -eq 'py.exe') -and
    $_.CommandLine -match 'oto_tarama|oto_bot'
}

# 2) Soyagaci: OTO python'un tum torunlari (chromedriver, brave...)
function Get-Soy($kokPidler, $hepsi) {
    $bulunan = New-Object System.Collections.Generic.HashSet[int]
    $sira = New-Object System.Collections.Queue
    foreach ($k in $kokPidler) { $sira.Enqueue($k) }
    while ($sira.Count -gt 0) {
        $ebeveyn = $sira.Dequeue()
        foreach ($c in ($hepsi | Where-Object { $_.ParentProcessId -eq $ebeveyn })) {
            if ($bulunan.Add([int]$c.ProcessId)) { $sira.Enqueue($c.ProcessId) }
        }
    }
    return $bulunan
}

$kokler = @($py | ForEach-Object { $_.ProcessId })
$soy = @()
if ($kokler.Count -gt 0) {
    $soyPid = Get-Soy $kokler $tumSurec
    $soy = $tumSurec | Where-Object { $soyPid.Contains([int]$_.ProcessId) }
    Write-Host ("  oto botunun soyundan {0} surec bulundu" -f ($soy | Measure-Object).Count)
}

# Once cocuklar, sonra python (ebeveyn olurse soyagaci kopar)
foreach ($s in ($soy | Where-Object { $_.Name -match 'chromedriver|brave|chrome' })) {
    try { Stop-Process -Id $s.ProcessId -Force -ErrorAction Stop } catch {}
}
foreach ($p in $py) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host ("  python durduruldu: PID {0}" -f $p.ProcessId)
    } catch {}
}
if (-not $py) { Write-Host "  calisan oto botu yok" }

Start-Sleep -Milliseconds 800

# 3) YETIMLER — ebeveyni artik yasamayan surecler. PC botu calisiyorsa
#    ATLA, cunku onun gecici profilli tarayicisi ayni markeri tasiyor.
if ($pcBot) {
    Write-Host "  yetim taramasi ATLANDI (PC botu calisiyor)." -ForegroundColor Yellow
    Write-Host "     Yetim birikirse PC botunu durdurup tekrar calistir."
} else {
    $canliPid = $tumSurec | ForEach-Object { [int]$_.ProcessId }

    $yetimDrv = $tumSurec | Where-Object {
        $_.Name -match 'chromedriver' -and
        -not ($canliPid -contains [int]$_.ParentProcessId)
    }
    foreach ($d in $yetimDrv) {
        try { Stop-Process -Id $d.ProcessId -Force -ErrorAction Stop } catch {}
    }
    $nd = ($yetimDrv | Measure-Object).Count
    if ($nd -gt 0) { Write-Host "  yetim chromedriver temizlendi: $nd" }

    $tar = $tumSurec | Where-Object { $_.Name -eq 'chrome.exe' -or $_.Name -eq 'brave.exe' }
    $botTar = $tar | Where-Object {
        $_.CommandLine -match $BOT_IZI -and
        -not ($canliPid -contains [int]$_.ParentProcessId)
    }
    foreach ($t in $botTar) {
        try { Stop-Process -Id $t.ProcessId -Force -ErrorAction Stop } catch {}
    }
    $nt = ($botTar | Measure-Object).Count
    if ($nt -gt 0) { Write-Host "  yetim bot tarayicisi temizlendi: $nt" }

    $kullanici = ($tar | Where-Object { -not ($_.CommandLine -match $BOT_IZI) } |
                  Measure-Object).Count
    Write-Host "  KULLANICININ tarayici surecleri korundu: $kullanici" -ForegroundColor Green
}

Write-Host "=== BITTI ===" -ForegroundColor Cyan
Write-Host "NOT: uzanti toplayicisi ve yerel sunucu bu betikten ETKILENMEZ."
Write-Host "     Toplayiciyi durdurmak icin: ayar.json -> durdur: true"
