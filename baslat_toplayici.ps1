# ══════════════════════════════════════════════════════════════════════
#  TOPLAYICIYI ISINMA ILE BASLAT
#
#  NEDEN VAR: 08.10.2026 18:06'da sekmesi kapanmis temiz profilde
#  kategori adresini DOGRUDAN actim ve oturum PX yedi. Oysa projenin
#  05.10 bulgusu bunu zaten soyluyordu:
#      "SOGUK GIRIS PX'in kok nedeni — cozum isinma turu:
#       ana sayfa -> bekle -> (kaydir) -> kategoriye gec"
#  Kendi notumu uygulamadigim icin bir mudahale daha harcandi. Bu betik
#  o sirayi mecbur kiliyor; bir daha elle acilmayacak.
#
#  NE YAPAR
#    1) Temiz profille (hesapsiz) Brave'i ANA SAYFADA acar
#    2) 90-150 sn rastgele bekler (insan gibi oturma suresi)
#    3) Kategori sayfasini acar — oturum artik ISINMIS
#    4) ayar.json -> durdur: false  (toplayici tur atmaya baslar)
#
#  KULLANIM
#    powershell -ExecutionPolicy Bypass -File baslat_toplayici.ps1
#    ya da: baslat_toplayici.bat
#
#  NOT: Bu betik PC botuna ve letgo'ya DOKUNMAZ; onlar kendi ayri
#  oturumlarinda calisir (08.10 dersi: yuk tek oturuma yigilmamali).
# ══════════════════════════════════════════════════════════════════════

$brave   = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
$profil  = "OtoKelepir"
$anaSayfa = "https://www.sahibinden.com/"
$kategori = "https://www.sahibinden.com/otomobil?sorting=date_desc&pagingSize=50"
$kok     = "C:\Users\AHMET1\Desktop\oto_kelepir"

if (-not (Test-Path $brave)) {
    Write-Host "Brave bulunamadi: $brave" -ForegroundColor Red
    exit 1
}

Write-Host "=== TOPLAYICI BASLATILIYOR (isinmali) ===" -ForegroundColor Cyan

# Toplayici isinma bitene kadar SUSSUN — ana sayfada tur atmasin diye
# (content script sadece kategori sayfalarinda calisir ama yine de
# durdur=true ile basliyoruz, kategori acilinca aciliyor).
try {
    $a = Get-Content "$kok\ayar.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host "  mevcut ayar: durdur=$($a.durdur) varyant=$($a.varyant_mod) tavan=$($a.tg_tur_tavan)"
} catch {
    Write-Host "  ayar.json okunamadi (varsayilanlar gecerli olacak)" -ForegroundColor Yellow
}

# 1) ANA SAYFA
Write-Host "  1/4 ana sayfa aciliyor (isinma)..."
Start-Process -FilePath $brave -ArgumentList "--profile-directory=$profil", $anaSayfa
Start-Sleep -Seconds 8

# 2) BEKLE — insan gibi oturma suresi
$bekle = Get-Random -Minimum 90 -Maximum 150
Write-Host "  2/4 $bekle saniye bekleniyor (sayfada oturma)..."
Start-Sleep -Seconds $bekle

# 3) KATEGORI
Write-Host "  3/4 kategori sayfasi aciliyor..."
Start-Process -FilePath $brave -ArgumentList "--profile-directory=$profil", $kategori
Start-Sleep -Seconds 12

# PX kontrolu — basligi oku
$denied = Get-Process brave -ErrorAction SilentlyContinue |
          Where-Object { $_.MainWindowTitle -match 'denied|Dogrulama|Doğrulama|just a moment' }
if ($denied) {
    Write-Host "  [!] DOGRULAMA/BLOK EKRANI gorundu — toplayici ACILMIYOR." -ForegroundColor Red
    Write-Host "      Oturumu dinlendir, sonra tekrar dene. PX kendi gecmiyor."
    exit 2
}

# 4) TOPLAYICIYI AC
Write-Host "  4/4 toplayici aciliyor (ayar.json -> durdur: false)"
& py -3.12 -c @'
import json
from pathlib import Path
p = Path(r"C:\Users\AHMET1\Desktop\oto_kelepir\ayar.json")
d = json.loads(p.read_text(encoding="utf-8"))
d["durdur"] = False
d.pop("_durdur_sebep", None)
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("   durdur = False")
'@

Write-Host "=== HAZIR ===" -ForegroundColor Green
Write-Host "Panel: http://127.0.0.1:8765/   (sunucu kapaliysa: py -3.12 sunucu.py)"
Write-Host "NOT: o sekmede GIRIS YAPMA — hesapsiz oturum, 2FA duvari cikmasin."
