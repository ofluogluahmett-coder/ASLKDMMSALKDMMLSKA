# ======================================================================
#  TOPLAYICIYI ISINMA ILE BASLAT
#
#  NEDEN VAR: 08.10.2026 18:06'da sekmesi kapanmis temiz profilde
#  kategori adresini DOGRUDAN actim ve oturum PX yedi. Oysa projenin
#  05.10 bulgusu bunu zaten soyluyordu:
#      "SOGUK GIRIS PX'in kok nedeni - cozum isinma turu:
#       ana sayfa -> bekle -> kategoriye gec"
#  Kendi notumu uygulamadigim icin bir mudahale harcandi. Bu betik o
#  sirayi mecbur kiliyor; kategori bir daha elle/dogrudan acilmayacak.
#
#  NE YAPAR
#    1) Temiz profille (hesapsiz) Brave'i ANA SAYFADA acar
#    2) 90-150 sn rastgele bekler (insan gibi oturma suresi)
#    3) Kategori sayfasini acar - oturum artik ISINMIS
#    4) ayar.json -> durdur: false  (toplayici tur atmaya baslar)
#
#  KULLANIM
#    powershell -ExecutionPolicy Bypass -File baslat_toplayici.ps1
#    ya da cift tikla: baslat_toplayici.bat
#
#  NOT 1: PC botuna ve letgo'ya DOKUNMAZ; onlar kendi ayri
#  oturumlarinda calisir (08.10 dersi: yuk tek oturuma yigilmamali).
#
#  NOT 2: BU DOSYA BILEREK TAMAMEN ASCII. Sebebi olculdu: dosya BOM'suz
#  UTF-8 yazilinca Windows PowerShell 5.1 onu ANSI (cp1254) okuyor ve
#  metin icindeki uzun tire (em dash) kivrik tirnak karakterine donusuyor; PowerShell
#  bunu DIZGE KAPATICI sayip betigi bozuyor ("string is missing the
#  terminator"). Turkce karakter ve tipografik isaret KULLANILMAYACAK.
# ======================================================================

$brave    = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
$profil   = "OtoKelepir"
$anaSayfa = "https://www.sahibinden.com/"
$kategori = "https://www.sahibinden.com/otomobil?sorting=date_desc&pagingSize=50"
$kok      = "C:\Users\AHMET1\Desktop\oto_kelepir"

if (-not (Test-Path $brave)) {
    Write-Host "Brave bulunamadi: $brave" -ForegroundColor Red
    exit 1
}

Write-Host "=== TOPLAYICI BASLATILIYOR (isinmali) ===" -ForegroundColor Cyan

try {
    $a = Get-Content "$kok\ayar.json" -Raw | ConvertFrom-Json
    Write-Host ("  mevcut ayar: durdur={0} varyant={1} tavan={2}" -f `
                $a.durdur, $a.varyant_mod, $a.tg_tur_tavan)
} catch {
    Write-Host "  ayar.json okunamadi (kod icindeki varsayilanlar gecerli)" -ForegroundColor Yellow
}

# 1) ANA SAYFA (isinma)
Write-Host "  1/4 ana sayfa aciliyor..."
Start-Process -FilePath $brave -ArgumentList "--profile-directory=$profil", $anaSayfa
Start-Sleep -Seconds 8

# 2) BEKLE - insan gibi oturma suresi
$bekle = Get-Random -Minimum 90 -Maximum 150
Write-Host ("  2/4 {0} saniye bekleniyor (sayfada oturma)..." -f $bekle)
Start-Sleep -Seconds $bekle

# 3) KATEGORI
Write-Host "  3/4 kategori sayfasi aciliyor..."
Start-Process -FilePath $brave -ArgumentList "--profile-directory=$profil", $kategori
Start-Sleep -Seconds 12

# PX / dogrulama kontrolu - pencere basligindan
$engel = Get-Process brave -ErrorAction SilentlyContinue |
         Where-Object { $_.MainWindowTitle -match 'denied|ogrulama|just a moment|Attention' }
if ($engel) {
    Write-Host "  [!] DOGRULAMA veya BLOK EKRANI gorundu." -ForegroundColor Red
    Write-Host "      Toplayici ACILMADI. Oturumu dinlendir, sonra tekrar dene."
    Write-Host "      PX kendiliginden gecmiyor (olculdu)."
    exit 2
}

# 4) TOPLAYICIYI AC
Write-Host "  4/4 toplayici aciliyor (ayar.json -> durdur: false)"
$pyKod = 'import json,sys' + "`n" +
         'from pathlib import Path' + "`n" +
         'p = Path(sys.argv[1])' + "`n" +
         'd = json.loads(p.read_text(encoding="utf-8"))' + "`n" +
         'd["durdur"] = False' + "`n" +
         'd["bekci_yenileme"] = True' + "`n" +
         'd.pop("_durdur_sebep", None)' + "`n" +
         'd.pop("_bekci_kapali_sebep", None)' + "`n" +
         'p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")' + "`n" +
         'print("   durdur=False, bekci_yenileme=True")'
& py -3.12 -c $pyKod "$kok\ayar.json"

Write-Host "=== HAZIR ===" -ForegroundColor Green
Write-Host "Panel: http://127.0.0.1:8765/   (sunucu kapaliysa: py -3.12 sunucu.py)"
Write-Host "UYARI: o sekmede GIRIS YAPMA. Hesapsiz oturum = 2FA duvari yok."
