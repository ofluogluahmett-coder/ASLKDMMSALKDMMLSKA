# ======================================================================
#  OTOMOBIL TOPLAYICISI - TEK TIKLA BASLAT
#
#  Bu betik her acilista elle ugrasmayi bitirmek icin var. Dort isi
#  sirayla yapar ve sonunda GERCEKTEN calistigini DOGRULAR:
#
#    1) Yerel sunucu (sunucu.py) kapaliysa baslatir
#    2) Temiz profille (hesapsiz) Brave'i ANA SAYFADA acar  [ISINMA]
#    3) 90-150 sn bekler, sonra kategori sayfasini acar
#    4) ayar.json -> durdur: false  ve 2 dakika icinde tur gelip
#       gelmedigini KONTROL EDER; gelmezse ne yapilacagini yazar
#
#  NEDEN ISINMA: 08.10.2026'da sekmesi kapanmis profilde kategori
#  adresini DOGRUDAN actim ve oturum PX yedi. Projenin 05.10 bulgusu:
#  "SOGUK GIRIS PX'in kok nedeni - cozum: ana sayfa -> bekle -> kategori".
#
#  NEDEN TEMIZ PROFIL: ana profildeki sahibinden oturumu 2 ASAMALI
#  DOGRULAMA istedi ve SMS kullanilmayan eski numaraya gidiyor. Hesapsiz
#  oturumda o duvar hic cikmiyor. O SEKMEDE GIRIS YAPILMAYACAK.
#
#  BU BETIK PC BOTUNA DOKUNMAZ. PC bileseni + konsol apex_predator'un
#  kendi botuyla (baslat.bat) toplanir - 08.10 aksami kullanici karari.
#
#  DOSYA TAMAMEN ASCII: BOM'suz UTF-8 + Turkce karakter, Windows
#  PowerShell 5.1'de dizge hatasina yol aciyor (olculdu). Tipografik
#  isaret ve Turkce harf KULLANILMAYACAK.
#
#  KULLANIM: baslat_toplayici.bat (cift tikla)
# ======================================================================

$brave    = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
$profil   = "OtoKelepir"
$anaSayfa = "https://www.sahibinden.com/"
$kategori = "https://www.sahibinden.com/otomobil?sorting=date_desc&pagingSize=50"
$kok      = "C:\Users\AHMET1\Desktop\oto_kelepir"
$durumUc  = "http://127.0.0.1:8765/durum"

if (-not (Test-Path $brave)) {
    Write-Host "Brave bulunamadi: $brave" -ForegroundColor Red
    exit 1
}

Write-Host "=== OTOMOBIL TOPLAYICISI BASLATILIYOR ===" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1/4
Write-Host "  1/4 yerel sunucu kontrol ediliyor..."
$sunucu = Get-CimInstance Win32_Process |
          Where-Object { $_.CommandLine -match 'sunucu\.py' }
if ($sunucu) {
    Write-Host "      zaten calisiyor (PID $($sunucu[0].ProcessId))"
} else {
    $p = Start-Process -FilePath "py" `
         -ArgumentList "-3.12","-u","sunucu.py" `
         -WorkingDirectory $kok `
         -RedirectStandardOutput "$kok\sunucu.log" `
         -RedirectStandardError "$kok\sunucu.err.log" `
         -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 4
    Write-Host "      baslatildi (PID $($p.Id))"
}
try {
    $null = Invoke-RestMethod $durumUc -TimeoutSec 6
    Write-Host "      sunucu cevap veriyor"
} catch {
    Write-Host "      [!] sunucu cevap vermiyor - devam ediliyor" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 2/4
Write-Host "  2/4 ana sayfa aciliyor (isinma)..."
Start-Process -FilePath $brave -ArgumentList "--profile-directory=$profil", $anaSayfa
Start-Sleep -Seconds 8

$bekle = Get-Random -Minimum 90 -Maximum 150
Write-Host ("      {0} saniye bekleniyor (sayfada oturma)" -f $bekle)
Start-Sleep -Seconds $bekle

# ---------------------------------------------------------------- 3/4
Write-Host "  3/4 kategori sayfasi aciliyor..."
Start-Process -FilePath $brave -ArgumentList "--profile-directory=$profil", $kategori
Start-Sleep -Seconds 12

$engel = Get-Process brave -ErrorAction SilentlyContinue |
         Where-Object { $_.MainWindowTitle -match 'denied|ogrulama|just a moment|Attention' }
if ($engel) {
    Write-Host "      [!] DOGRULAMA veya BLOK EKRANI gorundu." -ForegroundColor Red
    Write-Host "          Toplayici ACILMADI. Oturumu dinlendir (1-2 saat),"
    Write-Host "          sonra bu betigi tekrar calistir."
    Write-Host "          PX kendiliginden gecmiyor (olculdu)."
    exit 2
}

# ---------------------------------------------------------------- 4/4
Write-Host "  4/4 toplayici aciliyor..."
& py -3.12 "$kok\toplayici_ayar.py" ac
if ($LASTEXITCODE -ne 0) {
    Write-Host "      [!] ayar guncellenemedi." -ForegroundColor Red
    Write-Host "          Elle: py -3.12 toplayici_ayar.py ac"
    exit 3
}

# --- DOGRULAMA: 2 dakika icinde tur geliyor mu?
Write-Host "  ... tur bekleniyor (en fazla 2 dakika)"
$basla = Get-Date
$geldi = $false
while (((Get-Date) - $basla).TotalSeconds -lt 120) {
    Start-Sleep -Seconds 10
    try {
        $d = Invoke-RestMethod $durumUc -TimeoutSec 6
        if ($d.istek -ge 1) { $geldi = $true; break }
    } catch { }
}

if ($geldi) {
    Write-Host "=== CALISIYOR ===" -ForegroundColor Green
    Write-Host ("  tur={0}  yeni ilan={1}  hafiza={2}" -f `
                $d.son_tur, $d.yeni, $d.hafiza_ilan)
    Write-Host "  Panel: http://127.0.0.1:8765/"
    Write-Host "  O sekmeyi KAPATMA ve GIRIS YAPMA."
} else {
    Write-Host "=== TUR GELMEDI - muhtemel sebep: uzanti yuklu degil ===" -ForegroundColor Yellow
    Write-Host "  Acilan Brave penceresinde (profil: $profil):"
    Write-Host "    1) brave://extensions"
    Write-Host "    2) sag ustten Gelistirici modu"
    Write-Host "    3) Paketlenmemis yukle -> $kok\uzanti"
    Write-Host "    4) otomobil sekmesine don, F5"
    Write-Host "  Uzanti BIR KEZ yuklenince kalici olur; bir daha gerekmez."
}
