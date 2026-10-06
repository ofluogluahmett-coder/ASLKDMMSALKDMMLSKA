"""
OTOMOBIL TARAMA BOTU (sade)
PC bilesenleri botunun (sahibinden_bot.py) TARAMA cekirdeginin birebir
otomobil surumu. SADECE tarar + DB'ye yazar. Kelepir/Telegram YOK.

05.10.2026 — PX'E DENK GELMEME KATMANI eklendi. Amac PX'i COZMEK degil,
hic karsilasmamak: parametre disiplini, adaptif tempo, challenge devre
kesici, oturum tazeleme, es zamanlilik kilidi. Ayrinti: docs/px_kacinma.md

Calistirma:  py -3.12 -u oto_tarama.py       (MAX_TUR=0 -> sinirsiz)
DB:          oto_tarama.db  (tablo: oto_ilan)
"""
import os
import re
import sys
import time
import random
import sqlite3
import undetected_chromedriver as uc
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

ROOT       = Path(__file__).parent
DB_FILE    = ROOT / "oto_tarama.db"
BRAVE_PATH  = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# 06.10.2026 — TARAYICI SECIMI (kullanici gozlemi):
# Brave ELLE bile hard PX yiyor hale geldi; AYNI makinede AYNI IP'den
# Chrome sorunsuz taranabiliyor. Iki sonuc:
#   (a) Blok IP seviyesinde DEGIL — Chrome ayni IP'den giriyor.
#   (b) Damga Brave'in PARMAK IZINE yapismis. Bu, anlamadigimiz seyi
#       aciklar: her acilista TEMIZ gecici profil kullaniyoruz, cerez
#       tasimiyoruz, ama her yeni oturum DOGUSTAN bloklu geliyordu —
#       cunku tasidigimiz sey cerez degil, uc+Brave'in her seferinde
#       ayni urettigi imza. "Tur 1 gecer, tur 2 duvar" deseni de bu.
# TARAYICI=chrome ile damgasiz tarayiciya gecilir. Ayni zamanda TEMIZ
# LABORATUVAR: istek desenimiz mi sorunlu, yoksa sadece Brave mi yanmisti?
TARAYICI = os.getenv("TARAYICI", "brave").strip().lower()
TARAYICI_YOL = CHROME_PATH if TARAYICI == "chrome" else BRAVE_PATH
_TARAYICI_APP = {
    "brave": r"C:\Program Files\BraveSoftware\Brave-Browser\Application",
    "chrome": r"C:\Program Files\Google\Chrome\Application",
}

# PC botunda ANA_URL neydi, burada otomobil linki o.
ANA_URL = os.getenv("ANA_URL", "https://www.sahibinden.com/otomobil?sorting=date_desc")

# Kac tur taranacak? 0 = sinirsiz (surekli calis).
MAX_TUR = int(os.getenv("MAX_TUR", "0"))

# ==========================================================================
#  PX'E DENK GELMEME KATMANI (05.10.2026)
#  Felsefe: PX bir duvar degil bir SKOR. Skoru yukselten sey istek HACMI
#  degil, ANORMALLIK. Olculmus anormallikler asagida.
# ==========================================================================

# --- 1) PARAMETRE DISIPLINI ----------------------------------------------
# OLCUM (05.10.2026, temiz anonim oturum, tek istek):
#   ?sorting=date_desc    -> 21 ilan, sorunsuz (10 tur boyunca)
#   &_=<ms> (cache-bust)  -> sorunsuz (jQuery gelenegi, yoksayiliyor)
#   &pagingSize=50        -> ANINDA "Access to this page has been denied".
#                            Sonraki istek de bloklu: damga OTURUM/IP
#                            seviyesine cikti, TAZE PROFIL bile kurtarmadi.
#   &pagingSize=100       -> ayni sonuc.
# Ders: tanidik olmayan parametre = tek istekte yanmis IP. O yuzden URL'in
# parametreleri bu beyaz listeye karsi DOGRULANIR; listede olmayan bir
# parametre varsa bot HIC BASLAMAZ (ortak gelistirmede kazara IP yakmayi
# onler). Yeni parametreyi once TEK ISTEKLE, gozunun onunde dene.
# 06.10.2026 AKSAM — DUZELTME: pagingSize ZEHIRLI DEGIL, SITENIN KENDI
# PARAMETRESI. Canli DOM'da sayfa-boyu kontrolu bulundu (sayfanin EN ALTINDA):
#   <a class="paging-size Limit50Passive"
#      href="/otomobil?pagingOffset=0&pagingSize=50&sorting=date_desc">50</a>
# Yani "50" dugmesine basan HER kullanici bu adrese gidiyor. Sabah bu
# parametreyi "tek istekte IP yakiyor" diye listeden cikarmistim; yanlis
# atifti — blok parametreden degil, onu SOGUK GIRISTE (sifir gecmisli profil
# + dogrudan derin URL + cache-bust) denememden geliyordu.
# Isinmis oturumda sitenin kendi dugmesine TIKLAYARAK test edildi:
#   once : 22 ilan  /otomobil?sorting=date_desc
#   sonra: 52 ilan  /otomobil?sorting=date_desc&pagingSize=50   PX YOK
# pagingOffset de sitenin kendi parametresi (kullanici elle gezerken
# kullandi, PX yok).
#
# '_' (cache-bust) ise LISTEDE DEGIL. Olcum:
#   CACHE_BUST=0 -> 31 tur / 0 challenge (05.10 gece)
#   CACHE_BUST=1 -> 2. TURDA hard block (06.10 20:52)
# Tek fark cache-bust'ti. Mantikli: insan her istekte URL'i degistirmez;
# her istege farkli bir parametre eklemek "ayni sayfayi surekli yeniden
# iste" davranisinin imzasi. Tazelik icin cache-bust yerine tarayici
# cache'i zaten liste sayfasinda devre disi (sunucu no-cache gonderiyor):
# 31 turluk kosuda cache-bust OLMADAN da her tur 20 yeni ilan geldi.
GUVENLI_PARAMETRELER = {"sorting", "pagingSize", "pagingOffset"}
PARAMETRE_KONTROL = os.getenv("PARAMETRE_KONTROL", "1") != "0"

# --- 2) ADAPTIF TEMPO ----------------------------------------------------
# Taban periyot: turun TOPLAM suresi (tarama+mola) bu hedefte tutulur.
# 06.10.2026 — KULLANICI KARARI: "tarama araligi dar bir marjda rastgele
# oynasin (80, 83, 86, 81...), buyuk sicramalar olmasin; carpanli uzun
# molalari kaldir." Sabit periyot da istenmiyor: birebir ayni aralik
# (orn. her tur tam 80.0 sn) makine imzasidir, hafif jitter insanidir.
# Not: 80 sn, Aydin'in botundan (90-120) biraz hizli, eski temiz donemden
# (180-300) belirgin hizli, onceki ayarimizdan (50-75) ise YAVAS.
TEMEL_MIN = float(os.getenv("TEMEL_MIN", "77"))
TEMEL_MAX = float(os.getenv("TEMEL_MAX", "86"))
PERIYOT_TAVANI = 420.0
# Gece ilan akisi durur -> ayni tempoda taramak bedava risktir.
GECE_BASLA, GECE_BITIS = 2, 7
GECE_CARPANI = float(os.getenv("GECE_CARPANI", "2.5"))
# 06.10.2026 — KULLANICI KARARI: "yavaslama denen seyi komple kaldiralim,
# stabilite kadar HIZ da onemli." ADAPTIF=0 -> gece carpani ve bos-tur
# yavaslatmasi DEVRE DISI; tempo sadece TEMEL_MIN..TEMEL_MAX arasinda
# rastgele kalir. Challenge sonrasi mola (devre kesici) BU AYARDAN
# ETKILENMEZ — o mola damgali oturumu terk etmek icin, yavaslatma degil.
ADAPTIF = os.getenv("ADAPTIF", "0") != "0"   # 06.10: VARSAYILAN KAPALI (dar bant yeter)
# Ust uste bos tur (yeni ilan yok) -> kategori sogumus olabilir, yavasla.
# 06.10.2026 DUZELTME — bu kural TERS calisiyordu. Yogun saatte (21:00)
# `en_yeni_id` donuyor, cunku liste BAYAT; bot bunu "kategori sogudu" sanip
# molayi 66s->105s->180s'e cikariyordu. Yani tazeleme gerektiginde daha az
# tazeliyordu ve ekranda "bot durdu" gibi gorunuyordu.
# Yeni kural: ilk BOS_TUR_ESIK bos turda yavaslama YOK; sonrasinda nazik
# (en fazla ~1.5 kat). Gercek gece sakinligini GECE_CARPANI zaten hallediyor.
BOS_TUR_ESIK   = 6          # bu kadar bos tura kadar tempo DEGISMEZ
BOS_TUR_CARPAN = 1.15
BOS_TUR_TAVAN  = 3          # carpan en fazla 1.15^3 (~1.52)

# --- 3) CHALLENGE DEVRE KESICI -------------------------------------------
# Challenge gorunce YAPILMAYACAK sey: ayni oturumla tekrar denemek. Damgali
# oturum her istekte damgayi tazeler. Yapilacak: oturumu TERK et, katlanarak
# bekle, temiz profille don ve bir sure TEMKINLI git.
CHALLENGE_MOLA = [300, 900, 1800, 3600]     # 1., 2., 3., 4.+ ust uste challenge
TEMKINLI_TUR    = int(os.getenv("TEMKINLI_TUR", "12"))
TEMKINLI_CARPAN = 2.0

# --- 4) OTURUM TAZELEME --------------------------------------------------
# Uzun yasayan oturum PX cerezi biriktirir (PC botunda kanitlandi: haftalarca
# kullanilan kalici profil surekli CF yiyordu). Anonim modda tazeleme bedava:
# driver'i kapat, kisa bekle, yeni gecici profille ac. 0 = kapali.
OTURUM_TAZELE_DK = float(os.getenv("OTURUM_TAZELE_DK", "90"))

# --- 5) ES ZAMANLILIK ----------------------------------------------------
# Ayni IP'den es zamanli IKI oturum, tek oturumdan daha anormal bir desendir.
# oto_bot.py zaten oto_bot.lock tutuyor; onu kilitli bulursak baslamayiz.
ESZAMANLI_IZIN = os.getenv("ESZAMANLI_IZIN", "0") == "1"
KILIT_DOSYA     = ROOT / "oto_tarama.lock"
OTO_BOT_KILIDI  = ROOT / "oto_bot.lock"

# --- 6) INSAN GIBI GIRIS (05.10.2026 gece bulgusu) ----------------------
# KANIT: kullanicinin ELLE actigi Brave, AYNI IP ve AYNI DAKIKADA /otomobil'de
# serbest geziyor (siralama + ilan detayina girme, hic PX yok); bizim uc
# oturumu tek duz URL isteginde "denied" yiyor. Parmak izi denetimi
# (arac_parmak_izi.py) TEMIZ cikti: navigator.webdriver=False, cdc_ yok,
# plugins dolu, native imzalar yamasiz, userAgentData normal. Yani fark
# parmak izinde DEGIL, GIRIS BICIMINDE:
#   - sifir gecmisli yepyeni profil (hic cerez, hic gezinme gecmisi)
#   - dogrudan derin kategori URL'si (ana sayfadan gelmiyor, referer yok)
#   - uzerine cache-bust parametresi
#   - tek bir gorsel bile indirmiyor
# Insan boyle gezmez. Sabah 11 turun temiz gecmesi de bunu destekliyor: ILK
# tur cerezi kapmis, kalan 10 tur AYNI oturumu kullanmis. Simdi her oturum
# sifirdan soguk giris yapiyor.
#
# ISINMA=1      -> oturum basinda once ANA SAYFA acilir, insan temposunda
#                  beklenir, hafif scroll yapilir; cerezler (_px, cf_clearance)
#                  dogal yolla alinir; SONRA kategoriye gecilir.
# CACHE_BUST=0  -> URL'e '&_=<ms>' eklenmez (elle gezen insanda bu parametre
#                  yok). Bedeli: origin bayat liste dondurebilir.
# CDP_PORT=9222 -> yeni tarayici ACILMAZ; ELLE acilmis (cerezi oturmus,
#                  guvenilir) Brave'e baglanilir.
ISINMA      = os.getenv("ISINMA", "1") != "0"
# DAVRANIS=1: her turda sayfa okunduktan SONRA, mola suresi icinde insan
# gibi davran (scroll + fare hareketi + okuma molasi).
# NEDEN (06.10.2026, arac_izle.py ile olculdu): kullanicinin gercek
# oturumunda PX sensoru /QerrWGjI/xhr/api/v2/collector adresine 7 dakikada
# 38 kez davranis verisi gonderiyor. Bizim bot sayfayi 0.5 sn'de okuyup
# 80 saniye HIC KIPIRDAMADAN bekliyordu; sensorun PX'e raporu "sayfa acildi,
# insan yok" oluyordu. Isinma turunda davraniyoruz ama tarama turlarinda
# davranmiyorduk — oysa sensor oturumun TUM omrunu izliyor.
DAVRANIS    = os.getenv("DAVRANIS", "1") != "0"

# --- 7) PARTI-DUYARLI TARAMA (06.10.2026) -------------------------------
# OLCUM: ilanlar tek tek DUSMUYOR, PARTI halinde geliyor. Kesintisiz
# 31 turluk kosuda (dun gece, 0 challenge) desen:
#     00:21:39   2 ilan
#     00:29:01  20 ilan  (+442 sn)
#     00:35:51  20 ilan  (+409 sn)
#     00:43:16  20 ilan  (+444 sn)
#     00:47:33  20 ilan  (+256 sn)
# Arada 5-6 tur BOS donuyor. Iki sonuc:
#   (a) Partiler arasinda sik tarama BOSA gidiyor — kazanc yok, PX riski var.
#   (b) Her parti TAM 20 cikiyor; tesadufi degil, sayfa 22 ilan tutuyor ve
#       biz sadece 1. sayfayi okuyoruz. Yani gercek parti 20'den BUYUK ve
#       tasan kismi SISTEMATIK olarak kaybediyoruz. (Kullanici elle gezerken
#       "bir arka sayfaya kadar gidebiliyor" diye dogruladi.) Bir partideki
#       20 ilanin ID yayilimi 23M-143M araliga dagilmis: bunlar tek tek
#       yayinlanmis degil, BIRLIKTE onaylanip indekse basilmis ilanlar.
# TASARIM: saat varsayimi YOK (botun saat profili olculemiyor; sadece test
# saatlerinde acik oluyor). Sistem gordugune tepki verir:
#   - Tur, sayfanin PARTI_ESIK kadarini yeni getirdiyse -> parti dusmus,
#     hemen derin sayfayi (pagingOffset) oku; o da doluysa bir sonrakini.
#   - Bos turlarda hicbir sey degismez (sabit dar bant tempo).
# pagingOffset guvenli: kullanici ELLE pagingOffset=20 ile gezdi, PX yok
# (06.10, arac_izle.py ile dogrulandi).
# SAYFA_BOYU: 50 ise oturum basinda sayfa INSAN GIBI en alta kaydirilir ve
# sitenin kendi "50" dugmesine tiklanir; liste 52 ilan doner.
# Kazanc: olculen 28'lik parti TEK istege sigiyor -> derin sayfaya inme
# ihtiyaci buyuk olcude biter, toplam istek sayisi duser, kayip biter.
# 20 ise eski davranis (sayfa boyu degistirilmez).
SAYFA_BOYU = int(os.getenv("SAYFA_BOYU", "50"))

PARTI_DUYARLI = os.getenv("PARTI_DUYARLI", "1") != "0"
# Parti esigi ORAN tabanli: sayfa 22 de olabilir 52 de. Sayfanin bu
# oranindan fazlasi TAZE ise parti sayfayi doldurmus, tasma ihtimali var.
PARTI_ORAN    = float(os.getenv("PARTI_ORAN", "0.5"))
PARTI_ESIK    = int(os.getenv("PARTI_ESIK", "10"))   # alt sinir

# 06.10.2026 — "YENI" ILE "TAZE" AYNI SEY DEGIL.
# Olcum: 22:10 sonrasi toplanan 130 kaydin %36'si ID olarak 1 MILYAR+
# geride (orn. 1.19B, guncel max 1.344B). Bunlar yeni ilan degil, satici
# tarafindan "doping"le one cikarilmis ESKI ilanlar — tarih yenilendigi
# icin date_desc listesinin basinda cikiyorlar.
# Sonuc: "DB'de yok" olcutu parti algisini yaniltiyordu (bot DB'yi
# doldurdugu surece her sey yeni gorunuyor; 3. turda 50/52 "yeni" dedi,
# derin sayfa 0 getirdi = bosa istek).
# Dogru olcut SU SEVIYESI: gercekten taze ilan, onceki turun en yuksek
# ID'sinin USTUNDE olandir. Parti algisi artik buna bakiyor.
_SU_SEVIYESI = 0   # bu oturumda gorulen en yuksek ilan ID'si
PARTI_MAX_SAYFA = int(os.getenv("PARTI_MAX_SAYFA", "3"))  # 1. sayfa + 2 derin
# 06.10.2026 OLCUM — ART ARDA HIZLI ISTEK PX TETIKLIYOR:
#   Chrome (damgasiz), tur 1 OK -> 4-9 sn sonra derin sayfa -> PX
#   ayni oturumda 56 sn sonra tur 2 -> SORUNSUZ (21 ilan)
# Yani suclu "her ikinci istek" degil, ARALIK. Derin sayfalar da normal
# tur temposuna yakin araliklarla okunur.
PARTI_SAYFA_ARASI = (
    float(os.getenv("PARTI_SAYFA_ARASI_MIN", "30")),
    float(os.getenv("PARTI_SAYFA_ARASI_MAX", "50")),
)

# --- 8) YENILEME BICIMI (06.10.2026) ------------------------------------
# OLCUM (arac_izle.py, kullanicinin KENDI tarayicisi, 7 dk gezinme):
#   Document istegi : 11  (liste -> ilan detayi -> geri, farkli sayfalar)
#   XHR istegi      : 144 — bunlarin 13'u DOGRUDAN liste URL'si:
#       [XHR] /otomobil?sorting=date_desc
#       [XHR] /otomobil?pagingOffset=40&sorting=date_desc
# Yani insan sayfa 2'ye gecerken TAM SAYFA yuklemiyor; sitenin kendi
# XHR'ini atiyor ve DOM degisiyor. Bizim bot ise her turda AYNI liste
# URL'ine BELGE navigasyonu yapiyordu — insan trafiginde olmayan desen.
# Gozlem: hem anonim hem ISINMIS/attach oturumda ILK istek geciyor, IKINCI
# belge istegi bloklaniyor.
#
# YENILEME=xhr -> oturumda liste sayfasi BIR KEZ acilir; sonraki her
# tazeleme sayfanin kendi baglaminda fetch() ile yapilir (ayni cerez, ayni
# referer, X-Requested-With: XMLHttpRequest). Donen HTML parse edilip
# kartlar cikarilir; BELGE navigasyonu YOK.
# YENILEME=get -> eski davranis (her tur driver.get).
YENILEME = os.getenv("YENILEME", "get").strip().lower()
ISINMA_URL  = "https://www.sahibinden.com/"
CACHE_BUST  = os.getenv("CACHE_BUST", "0") != "0"   # 06.10: VARSAYILAN KAPALI (bkz. yukarisi)
CDP_PORT    = os.getenv("CDP_PORT", "").strip()

# HIZ: gorsel/font/tracker byte'larini engelle (ilan verisi DOM'da kalir).
# DIKKAT: "hic gorsel indirmeyen istemci" insan-disi bir desen. Attach
# modunda (gercek tarayici) varsayilan KAPALI — o tarayici normal gozukmeli.
# 06.10.2026 — VARSAYILAN KAPALI: gercek oturum olculdu, 22 font + 5
# gorsel indiriyor. "Hic gorsel istemeyen istemci" insan disi bir desen
# ve hiz faydasi da kalmadi (yukleme zaten 0.4-1.1 sn).
GORSEL_BLOK    = os.getenv("GORSEL_BLOK", "0") != "0"
# 06.10.2026 — VARSAYILAN KAPALI. Kullanicinin kendi tarayicisi pasif
# dinlendi (arac_izle.py): sunucu liste sayfasini
#   cache-control: no-cache, no-store, must-revalidate
#   cf-cache-status: DYNAMIC
# ile gonderiyor ve 7 dakikalik gezinmede TEK BIR 304 yok. Yani sayfa
# hicbir zaman cache'lenmiyordu -> cache'i kapatmanin tazelik faydasi SIFIR,
# riski ise olculdu (cache_disabled=1 ile 2. turda hard block).
CACHE_DISABLED = os.getenv("CACHE_DISABLED", "0") != "0"
BLOCK_URLS = [
    "*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.svg",
    "*shbdn.com/photos*", "*.woff", "*.woff2", "*.ttf",
    "*google-analytics*", "*googletagmanager*", "*doubleclick*",
    "*facebook.net*", "*hotjar*", "*criteo*",
]

# Oturumun AKTIF liste URL'si. Sayfa boyu buyutulurse (sitenin kendi
# dugmesine tiklayarak) bu URL pagingSize icerir ve tum turlar bunu kullanir.
AKTIF_URL = ANA_URL

gorulmus = set()   # bu oturumda islenen data-id'ler
_istek = 0         # bu calismada atilan sayfa istegi sayisi
_kilit_fh = None

# uc'nin kapanista cift quit() denemesi "Chrome.__del__ / WinError 6" traceback'i
# basiyor (is bittikten SONRA, zararsiz ama crash gibi gorunuyor). Susturuyoruz.
uc.Chrome.__del__ = lambda self: None


# -- Guvenlik kontrolleri ---------------------------------------------------
def url_dogrula(url):
    """URL'de beyaz listede OLMAYAN parametre varsa calismayi reddet."""
    if not PARAMETRE_KONTROL:
        return
    p = parse_qs(urlparse(url).query)
    yabanci = sorted(set(p) - GUVENLI_PARAMETRELER)
    if yabanci:
        print(f"[DUR] URL'de olculmemis parametre var: {', '.join(yabanci)}")
        print(f"      Guvenli liste: {', '.join(sorted(GUVENLI_PARAMETRELER))}")
        print("      OLCUM: pagingSize TEK ISTEKTE IP'yi yakti (hard block).")
        print("      Yeni parametreyi once elle, tek istekle dene. Yine de")
        print("      calistirmak icin: PARAMETRE_KONTROL=0")
        sys.exit(2)


def kilit_al():
    """Tek instance + oto_bot ile es zamanli calismayi engelle."""
    global _kilit_fh
    try:
        import msvcrt
    except ImportError:
        return True
    if not ESZAMANLI_IZIN and OTO_BOT_KILIDI.exists():
        try:
            fh = open(OTO_BOT_KILIDI, "a+")
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)   # bos, serbest
            except OSError:
                print("[DUR] oto_bot.py calisiyor (oto_bot.lock kilitli).")
                print("      Ayni IP'den es zamanli iki oturum PX skorunu yukseltir.")
                print("      Once onu kapat, ya da ESZAMANLI_IZIN=1 ver.")
                fh.close()
                return False
            fh.close()
        except OSError:
            pass
    try:
        _kilit_fh = open(KILIT_DOSYA, "a+")
        msvcrt.locking(_kilit_fh.fileno(), msvcrt.LK_NBLCK, 1)
        _kilit_fh.seek(0)
        _kilit_fh.truncate()
        _kilit_fh.write(f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}\n")
        _kilit_fh.flush()
    except OSError:
        print("[DUR] Baska bir oto_tarama.py zaten calisiyor (oto_tarama.lock).")
        return False
    return True


# -- DB --------------------------------------------------------------------
def db_baglan():
    con = sqlite3.connect(DB_FILE, timeout=10.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=10000")
    return con


def db_hazirla():
    with db_baglan() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS oto_ilan (
                ilan_id   TEXT PRIMARY KEY,
                baslik    TEXT,
                fiyat     REAL,
                yil       TEXT,
                km        TEXT,
                url       TEXT,
                ilk_gorme TEXT,
                son_gorme TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_oto_ilk ON oto_ilan(ilk_gorme)")
    print(f"DB hazir: {DB_FILE.name}")


def ilan_kaydet(ilan_id, baslik, fiyat, yil, km, url):
    """Yeni ise ekler (True doner), varsa sadece son_gorme/fiyat gunceller (False)."""
    simdi = datetime.now().isoformat(timespec="seconds")
    with db_baglan() as con:
        var = con.execute("SELECT 1 FROM oto_ilan WHERE ilan_id=?", (ilan_id,)).fetchone()
        if var:
            con.execute("UPDATE oto_ilan SET fiyat=?, son_gorme=? WHERE ilan_id=?",
                        (fiyat, simdi, ilan_id))
            return False
        con.execute(
            "INSERT INTO oto_ilan (ilan_id, baslik, fiyat, yil, km, url, "
            "ilk_gorme, son_gorme) VALUES (?,?,?,?,?,?,?,?)",
            (ilan_id, baslik, fiyat, yil, km, url, simdi, simdi))
        return True


# -- Tarayici yardimcilari (PC botundan birebir) ---------------------------
def _brave_major_version() -> int:
    """Secili tarayicinin ana surumu (Application klasorunden)."""
    app = _TARAYICI_APP.get(TARAYICI, _TARAYICI_APP["brave"])
    try:
        surumler = [int(re.match(r"^(\d+)\.", d).group(1))
                    for d in os.listdir(app)
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", d)]
        return max(surumler) if surumler else 153
    except Exception:
        return 153


def _sayfa_bekle(driver, timeout=12):
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".searchResultsItem")))
    except Exception:
        pass


def _metin(item, secici):
    try:
        return item.find_element(By.CSS_SELECTOR, secici).text.strip()
    except Exception:
        return ""


def _port_dinliyor(p):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.7)
    try:
        return s.connect_ex(("127.0.0.1", int(p))) == 0
    finally:
        s.close()


def isin(driver, sessiz=False):
    """Oturumu insan gibi baslat: ONCE ana sayfa, bekle, hafif scroll.

    Amac cerezleri (_px, cf_clearance) dogal yolla almak ve kategoriye
    "ana sayfadan gelen" bir oturumla girmek. Soguk giris (dogrudan derin
    kategori URL'si) 05.10.2026 gecesi tek istekte blok yedi."""
    if not ISINMA:
        return
    try:
        driver.get(ISINMA_URL)
        time.sleep(random.uniform(4, 9))         # goz gezdirme
        for _ in range(random.randint(1, 3)):    # hafif scroll
            driver.execute_script(
                "window.scrollBy(0, %d);" % random.randint(250, 700))
            time.sleep(random.uniform(0.6, 1.6))
        time.sleep(random.uniform(1.5, 3.5))
        if not sessiz:
            print("ISINMA: ana sayfa gezildi, cerezler alindi "
                  "(%d cerez)" % len(driver.get_cookies()))
    except Exception as e:
        print("[UYARI] isinma yapilamadi: %s" % e)


def insan_gibi_davran(driver, pencere_sn):
    """Mola suresinin bir kismini insan gibi gecir: scroll + fare + okuma.

    PX sensoru oturum boyunca davranis verisi topluyor (olculdu: gercek
    oturumda 7 dk'da 38 collector POST'u). Hic etkilesim olmayan bir oturum
    "sayfa acildi, insan yok" diye raporlanir. Buradaki hareketler GERCEK
    girdi degil ama sensorun okudugu DOM/olay seviyesinde izleri birakir:
    scroll pozisyonu, mousemove olaylari, odak.

    pencere_sn: bu is icin ayrilan sure (molanin bir kismi).
    Hata durumunda sessizce doner — tarama akisini ASLA engellemez."""
    if not DAVRANIS or pencere_sn <= 2:
        return 0.0
    t0 = time.monotonic()
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys
        govde = driver.find_element(By.CSS_SELECTOR, "body")
        for _ in range(random.randint(2, 4)):
            if time.monotonic() - t0 > pencere_sn:
                break
            # Scroll: tek yonlu desen bot imzasi -> karisik
            if random.random() < 0.75:
                driver.execute_script(
                    "window.scrollBy(0, %d);" % random.randint(200, 650))
            else:
                driver.execute_script(
                    "window.scrollBy(0, -%d);" % random.randint(120, 400))
            time.sleep(random.uniform(0.7, 2.0))
            # Fare: ara noktali kucuk hareketler (mousemove olaylari)
            try:
                zincir = ActionChains(driver)
                zincir.move_to_element_with_offset(
                    govde, random.randint(60, 700), random.randint(60, 420))
                for _ in range(random.randint(1, 3)):
                    zincir.move_by_offset(random.randint(-90, 90),
                                          random.randint(-60, 60))
                zincir.perform()
            except Exception:
                pass
            time.sleep(random.uniform(0.4, 1.3))
        # Okuma molasi (sayfa uzerinde kal, sensor veri gondersin)
        kalan = pencere_sn - (time.monotonic() - t0)
        if kalan > 0:
            time.sleep(min(kalan, random.uniform(2.0, 6.0)))
    except Exception:
        pass
    return time.monotonic() - t0


# Sitenin kendi sayfa-boyu dugmesi (canli DOM'dan alindi)
SAYFA_BOYU_SECICI = "a.paging-size.Limit%dPassive"


def _url_offset(url, offset):
    """URL'deki pagingOffset'i degistir/ekle (digerlerine dokunmadan)."""
    temiz = re.sub(r"[?&]pagingOffset=\d+", "", url)
    ayirac = "&" if "?" in temiz else "?"
    return f"{temiz}{ayirac}pagingOffset={offset}"


def sayfa_boyu_ayarla(driver, sessiz=False):
    """Sayfa boyunu INSAN YOLUYLA buyut: sayfaya gir, en alta kadar
    kademeli kaydir, dugmeyi gorus alanina al, tikla.

    Neden kaydirma sart: o kontrol sayfanin EN ALTINDA. Hic kaydirmadan
    gorus alanina girmemis bir ogeye tiklamak tek basina otomasyon imzasi
    (kullanici onerisi, 06.10.2026).

    Doner: yeni liste URL'si (pagingSize dahil) veya None."""
    if SAYFA_BOYU <= 20:
        return None
    try:
        driver.get(ANA_URL)
        _sayfa_bekle(driver, timeout=12)
        time.sleep(random.uniform(1.5, 3.0))          # sayfaya goz at

        # Kademeli olarak en alta kaydir (tek hamlede ziplama bot imzasi)
        for _ in range(random.randint(4, 7)):
            driver.execute_script("window.scrollBy(0, %d);"
                                  % random.randint(400, 900))
            time.sleep(random.uniform(0.5, 1.4))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(1.0, 2.5))

        secici = SAYFA_BOYU_SECICI % SAYFA_BOYU
        try:
            dugme = driver.find_element(By.CSS_SELECTOR, secici)
        except Exception:
            if not sessiz:
                print(f"[UYARI] sayfa boyu dugmesi bulunamadi ({secici}) — "
                      f"20'lik liste ile devam")
            return None
        # Dugmeyi gorus alaninin ortasina al, sonra tikla
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", dugme)
        time.sleep(random.uniform(0.6, 1.6))
        dugme.click()
        _sayfa_bekle(driver, timeout=12)
        time.sleep(random.uniform(1.0, 2.0))

        n = len(driver.find_elements(By.CSS_SELECTOR, ".searchResultsItem"))
        yeni_url = driver.current_url.split("&_=")[0]
        if n > 25:
            if not sessiz:
                print(f"SAYFA BOYU: {SAYFA_BOYU} secildi (en alta kaydirip "
                      f"tiklayarak) — sayfada {n} ilan")
            return yeni_url
        if not sessiz:
            print(f"[UYARI] tiklamadan sonra {n} ilan — 20'lik liste ile devam")
    except Exception as e:
        print(f"[UYARI] sayfa boyu ayarlanamadi: {str(e)[:100]}")
    return None


def surucu_olustur(sessiz=False):
    # --- ATTACH MODU: yeni tarayici acma, ELLE acilmis olana baglan ---
    # Gerekce: kullanicinin kendi Brave'i ayni IP'de /otomobil'de serbest
    # geziyor. O tarayicinin kimligi ve cerezleri guvenilir durumda; biz
    # kendi soguk oturumumuzu kurmak yerine onu kullanabiliriz.
    if CDP_PORT:
        from selenium import webdriver as _wd
        if not _port_dinliyor(CDP_PORT):
            print("[DUR] 127.0.0.1:%s dinlenmiyor — once Brave'i debug portuyla"
                  % CDP_PORT)
            print("      ac: baslat_brave_debug.bat")
            raise SystemExit(5)
        _o = _wd.ChromeOptions()
        _o.add_experimental_option("debuggerAddress", "127.0.0.1:%s" % CDP_PORT)
        _o.page_load_strategy = "eager"
        driver = _wd.Chrome(options=_o)
        # Kullanicinin kendi sekmelerine DOKUNMA: bot kendine yeni sekme acar.
        try:
            driver.switch_to.new_window("tab")
            if not sessiz:
                print("ATTACH: bot kendi sekmesini acti (senin sekmelerin "
                      "oldugu gibi kaliyor)")
        except Exception as e:
            print("[UYARI] yeni sekme acilamadi, mevcut sekme kullanilacak: %s"
                  % str(e)[:80])
        if not sessiz:
            print("ATTACH: 127.0.0.1:%s — elle acilmis Brave'e baglanildi "
                  "(uc launch YOK, cerezler o tarayicinin)" % CDP_PORT)
        return driver

    options = uc.ChromeOptions()
    options.add_argument("--disable-dev-shm-usage")
    options.page_load_strategy = "eager"

    surum = _brave_major_version()
    if not sessiz:
        print(f"Tarayici: {TARAYICI.upper()} (major surum {surum})")
        # ANONIM MOD: user_data_dir YOK -> her acilista temiz gecici profil.
        # (PC botunda CF 'basili tut' spam'inin cozumu tam buydu.)
        print("Anonim mod: temiz gecici profil (kalici profil damgasi yok).")
    driver = uc.Chrome(
        options=options,
        browser_executable_path=TARAYICI_YOL,
        version_main=surum,
        no_sandbox=False,
    )
    try:
        if GORSEL_BLOK or CACHE_DISABLED:
            driver.execute_cdp_cmd("Network.enable", {})
        if GORSEL_BLOK:
            driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": BLOCK_URLS})
        if CACHE_DISABLED:
            driver.execute_cdp_cmd("Network.setCacheDisabled",
                                   {"cacheDisabled": True})
        if not sessiz:
            print("CDP: gorsel_blok=%s | cache_disabled=%s"
                  % ("acik" if GORSEL_BLOK else "KAPALI",
                     "acik" if CACHE_DISABLED else "KAPALI"))
    except Exception as e:
        print(f"[UYARI] CDP setup kurulamadi: {e}")

    # PX GEC ENTEGRASYONU: uc'nin sectigi gercek debug portunu dosyaya yaz.
    # px_gec.py bu dosyadan okuyup ayni tarayiciya baglanir.
    try:
        adres = driver.capabilities["goog:chromeOptions"]["debuggerAddress"]
        (ROOT / "cdp_port.txt").write_text(adres.split(":")[-1], encoding="utf-8")
        if not sessiz:
            print(f"CDP debug adresi: {adres}  (cdp_port.txt yazildi)")
    except Exception as e:
        print(f"[UYARI] debug portu okunamadi: {e}")
    isin(driver, sessiz)       # soguk giris yapma: once ana sayfa
    # Sayfa boyunu insan yoluyla buyut (en alta kaydir + sitenin dugmesi)
    global AKTIF_URL
    _u = sayfa_boyu_ayarla(driver, sessiz)
    AKTIF_URL = _u or ANA_URL
    return driver


def surucu_kapat(driver):
    """uc ile ACILAN tarayiciyi kapatir.

    ATTACH modunda HICBIR SEY YAPMAZ: o tarayici kullanicinin kendi
    tarayicisi, bot onu kapatamaz (06.10.2026 — quit() cagrisi senin
    pencerelerini kapatma riskiydi)."""
    if CDP_PORT:
        return
    try:
        driver.quit()
    except Exception:
        pass


# -- Challenge tespiti -----------------------------------------------------
CF_ISARET = (
    "just a moment", "bir dakika", "dakika lutfen", "baglantiniz kontrol",
    "bağlantınız kontrol", "kontrol ediliyor", "basili tut", "basılı tut",
    "checking your browser", "attention required", "cloudflare",
    "access to this page has been denied", "erisim engellendi",
    "sorry, you have been blocked", "verify you are human", "px-captcha",
)


# 06.10.2026 — CF ve PX AYRI SEYLER, ayri tepki gerektiriyor:
#   CF (Cloudflare "Bir dakika lutfen" / "Just a moment"):
#       kendiliginden 5-46 sn'de GECIYOR. Yapilacak: BEKLE, tiklama,
#       oturumu TERK ETME. Eski kod bunu hard block sanip sapasaglam
#       oturumu cope atiyordu (attach modunda kullanicinin tarayicisini!).
#   PX ("Access to this page has been denied" / px-captcha):
#       hard block. Oturum damgalandi; her yeni istek damgayi tazeler.
#       Yapilacak: oturumu birak, katlanan mola, temiz profille don.
PX_ISARET = ("access to this page has been denied", "px-captcha",
             "sorry, you have been blocked", "erisim engellendi",
             "basili tut", "basılı tut")
CF_SADECE = ("just a moment", "bir dakika", "dakika lutfen", "dakika lütfen",
             "checking your browser", "baglantiniz kontrol",
             "bağlantınız kontrol", "kontrol ediliyor", "attention required",
             "verify you are human")


def challenge_turu(driver):
    """None | 'cf' | 'px'"""
    basl = (driver.title or "").lower()
    govde = ""
    try:
        govde = driver.page_source[:6000].lower()
    except Exception:
        pass
    imza = basl + " " + govde
    if any(k in imza for k in PX_ISARET):
        return "px"
    if any(k in basl for k in CF_SADECE) or "cf-browser-verification" in govde:
        return "cf"
    return None


def cf_bekle(driver, max_bekleme=90):
    """CF kendiliginden gecene kadar bekle (TIKLAMA YOK). True = gecti."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_bekleme:
        time.sleep(5)
        try:
            if driver.find_elements(By.CSS_SELECTOR, ".searchResultsItem"):
                return True
            if challenge_turu(driver) is None:
                return True
        except Exception:
            pass
    return False


def hedef_periyot(bos_tur, temkinli_kalan):
    """Turun TOPLAM suresi icin hedef (sn). Risk arttikca uzar."""
    p = random.uniform(TEMEL_MIN, TEMEL_MAX)
    if not ADAPTIF:
        return p                      # duz rastgele tempo, carpan yok
    if bos_tur >= BOS_TUR_ESIK:
        p *= BOS_TUR_CARPAN ** min(bos_tur - BOS_TUR_ESIK + 1, BOS_TUR_TAVAN)
    if temkinli_kalan > 0:
        p *= TEMKINLI_CARPAN
    if GECE_BASLA <= datetime.now().hour < GECE_BITIS:
        p *= GECE_CARPANI
    return min(p, PERIYOT_TAVANI)


# Sayfanin KENDI baglaminda fetch(): sitenin sayfalama istegiyle ayni
# bicim. Donen HTML'den kartlari JS tarafinda cikarip JSON veriyoruz —
# boylece DOM'u degistirmeye (ve sayfayi bozmaya) gerek kalmiyor.
XHR_JS = r"""
const url = arguments[0];
const bitti = arguments[arguments.length - 1];
fetch(url, {
  method: "GET",
  credentials: "include",
  headers: {"X-Requested-With": "XMLHttpRequest"}
}).then(r => r.text().then(t => ({durum: r.status, metin: t})))
  .then(({durum, metin}) => {
    const kap = document.createElement("div");
    kap.innerHTML = metin;
    const kartlar = kap.querySelectorAll(".searchResultsItem");
    const cikti = [];
    kartlar.forEach(k => {
      const al = s => { const e = k.querySelector(s); return e ? e.textContent.trim() : ""; };
      const attr = [];
      k.querySelectorAll(".searchResultsAttributeValue").forEach(
        a => { const v = a.textContent.trim(); if (v) attr.push(v); });
      const a = k.querySelector(".searchResultsTitleValue a");
      cikti.push({
        id: k.getAttribute("data-id") || "",
        baslik: al(".searchResultsTitleValue"),
        fiyat: al(".searchResultsPriceValue"),
        attr: attr,
        href: a ? a.href : ""
      });
    });
    bitti({durum: durum, uzunluk: metin.length, kartlar: cikti,
           challenge: /denied|px-captcha|basili tut|bas\u0131l\u0131 tut/i.test(metin.slice(0, 4000))});
  })
  .catch(e => bitti({hata: String(e)}));
"""


def xhr_liste_cek(driver, url, timeout=25):
    """Sayfanin kendi baglaminda fetch ile listeyi cek. Doner: dict/None."""
    try:
        driver.set_script_timeout(timeout)
        return driver.execute_async_script(XHR_JS, url)
    except Exception as e:
        print("[UYARI] XHR cekme hatasi: %s" % str(e)[:120])
        return None


def xhr_kartlari_isle(kartlar):
    """xhr_liste_cek ciktisini DB'ye yaz. Doner: (yeni, guncel, en_yeni_id)"""
    yeni = guncel = 0
    en_yeni_id = 0
    for k in kartlar or []:
        try:
            ilan_id = (k.get("id") or "").strip()
            if not ilan_id or not ilan_id.isdigit():
                continue
            en_yeni_id = max(en_yeni_id, int(ilan_id))
            if ilan_id in gorulmus:
                continue
            baslik = (k.get("baslik") or "").strip()
            fiyat_s = re.sub(r"[^\d]", "", k.get("fiyat") or "")
            if not baslik or not fiyat_s:
                continue
            attr = k.get("attr") or []
            yil = attr[0] if len(attr) >= 1 else ""
            km = attr[1] if len(attr) >= 2 else ""
            url = (k.get("href") or "").strip() or \
                f"https://www.sahibinden.com/ilan/{ilan_id}/detay"
            if ilan_kaydet(ilan_id, baslik, float(fiyat_s), yil, km, url):
                yeni += 1
            else:
                guncel += 1
            gorulmus.add(ilan_id)
        except Exception:
            continue
    return yeni, guncel, en_yeni_id


def sayfa_isle(items, su_seviyesi=None):
    """Bir liste sayfasindaki kartlari DB'ye yazar.

    su_seviyesi verilirse, ID'si bunun USTUNDE olan kartlar 'taze' sayilir
    (gercekten yeni yayinlanmis). Verilmezse taze=0 doner.
    Doner: (yeni, guncel, en_yeni_id, taze)"""
    yeni = guncel = taze = 0
    en_yeni_id = 0
    for item in items:
        try:
            ilan_id = item.get_attribute("data-id")
            if not ilan_id or not ilan_id.isdigit():
                continue
            _idn = int(ilan_id)
            en_yeni_id = max(en_yeni_id, _idn)
            if su_seviyesi is not None and _idn > su_seviyesi:
                taze += 1
            if ilan_id in gorulmus:
                continue

            baslik = _metin(item, ".searchResultsTitleValue")
            fiyat_metin = _metin(item, ".searchResultsPriceValue")
            fiyat_s = re.sub(r"[^\d]", "", fiyat_metin)
            if not baslik or not fiyat_s:
                continue
            fiyat = float(fiyat_s)

            yil = km = ""
            try:
                attrs = item.find_elements(
                    By.CSS_SELECTOR, ".searchResultsAttributeValue")
                degerler = [a.text.strip() for a in attrs if a.text.strip()]
                if len(degerler) >= 1:
                    yil = degerler[0]
                if len(degerler) >= 2:
                    km = degerler[1]
            except Exception:
                pass

            try:
                href = item.find_element(
                    By.CSS_SELECTOR,
                    ".searchResultsTitleValue a").get_attribute("href")
            except Exception:
                href = None
            url = href or f"https://www.sahibinden.com/ilan/{ilan_id}/detay"

            if ilan_kaydet(ilan_id, baslik, fiyat, yil, km, url):
                yeni += 1
            else:
                guncel += 1
            gorulmus.add(ilan_id)
        except Exception:
            continue
    return yeni, guncel, en_yeni_id, taze


def derin_sayfalari_oku(driver, tur):
    """Parti dustugunde 2. ve 3. sayfayi oku (tasan ilanlari kurtar).

    Doner: (toplam_yeni, okunan_sayfa_sayisi)"""
    global _istek
    toplam_yeni = 0
    okunan = 0
    adim = SAYFA_BOYU if SAYFA_BOYU > 20 else 20
    for sayfa in range(2, PARTI_MAX_SAYFA + 1):
        offset = (sayfa - 1) * adim
        time.sleep(random.uniform(*PARTI_SAYFA_ARASI))   # insan temposu
        try:
            driver.get(_url_offset(AKTIF_URL, offset))
            _istek += 1
            _sayfa_bekle(driver, timeout=12)
            items = driver.find_elements(By.CSS_SELECTOR, ".searchResultsItem")
            _t = challenge_turu(driver) if not items else None
            if _t:
                print(f"[TUR {tur}]   derin sayfa {sayfa}: "
                      f"{'CF' if _t == 'cf' else 'PX'} — derinlesme durduruldu")
                return toplam_yeni, okunan
            yeni, guncel, _, _t = sayfa_isle(items)
            okunan += 1
            toplam_yeni += yeni
            print(f"[TUR {tur}]   derin sayfa {sayfa} (offset={offset}): "
                  f"ilan={len(items)} yeni={yeni}")
            # Bu sayfa da doluysa devam; degilse partinin sonuna geldik
            if yeni < max(PARTI_ESIK, int(PARTI_ORAN * max(len(items), 1))):
                break
        except Exception as e:
            print(f"[TUR {tur}]   derin sayfa {sayfa} HATA: {str(e)[:80]}")
            break
    return toplam_yeni, okunan


# -- Ana dongu -------------------------------------------------------------
def pusuya_yat():
    global _istek
    url_dogrula(ANA_URL)
    if not kilit_al():
        return
    db_hazirla()
    driver = surucu_olustur()
    oturum_basla = time.monotonic()
    limit_yazi = f"{MAX_TUR} tur" if MAX_TUR else "sinirsiz"
    print(f"OTO TARAMA basladi. Taban periyot: ~{TEMEL_MIN:.0f}-{TEMEL_MAX:.0f} sn | "
          f"Limit: {limit_yazi}")
    print(f"PX kacinma: parametre_kontrol={'acik' if PARAMETRE_KONTROL else 'KAPALI'} | "
          f"oturum_tazeleme={OTURUM_TAZELE_DK:.0f}dk | gece x{GECE_CARPANI} | "
          f"challenge_molasi={CHALLENGE_MOLA}")
    print("Tempo: %.0f-%.0f sn | adaptif=%s"
          % (TEMEL_MIN, TEMEL_MAX, "acik" if ADAPTIF else "KAPALI"))
    print("Insan modu: isinma=%s | cache_bust=%s | mod=%s"
          % ("acik" if ISINMA else "KAPALI",
             "acik" if CACHE_BUST else "KAPALI",
             ("ATTACH:" + CDP_PORT) if CDP_PORT else "uc-launch"))
    print(f"Sayfa boyu: {SAYFA_BOYU} | parti esigi: sayfanin %%%d'i "
          f"(min %d)" % (int(PARTI_ORAN * 100), PARTI_ESIK))
    print(f"URL: {AKTIF_URL}")

    tur = 0
    bos_tur = 0            # ust uste yeni ilan gelmeyen tur sayisi
    challenge_seri = 0     # ust uste challenge sayisi
    temkinli_kalan = 0     # kac tur daha temkinli gidilecek
    while True:
        if MAX_TUR and tur >= MAX_TUR:
            with db_baglan() as con:
                toplam = con.execute("SELECT COUNT(*) FROM oto_ilan").fetchone()[0]
            print(f"\n=== {MAX_TUR} TUR BITTI === DB'deki toplam ilan: {toplam} "
                  f"({DB_FILE.name}) | toplam istek: {_istek}")
            surucu_kapat(driver)
            return
        tur += 1
        tur_basla = time.monotonic()

        # OTURUM TAZELEME: uzun yasayan oturum PX cerezi biriktirir.
        if OTURUM_TAZELE_DK and (time.monotonic() - oturum_basla) > OTURUM_TAZELE_DK * 60:
            print(f"[TUR {tur}] oturum {OTURUM_TAZELE_DK:.0f}dk doldu — "
                  f"temiz profille yeniden aciliyor")
            surucu_kapat(driver)
            time.sleep(random.uniform(20, 40))
            driver = surucu_olustur(sessiz=True)
            oturum_basla = time.monotonic()
            gorulmus.clear()

        try:
            # Cache-buster: her tur farkli URL -> origin taze cevaba zorlanir.
            # CACHE_BUST=0 ise eklenmez (elle gezen insanda bu parametre yok).
            t0 = time.time()
            _url = (f"{AKTIF_URL}&_={int(time.time() * 1000)}" if CACHE_BUST
                    else AKTIF_URL)
            driver.get(_url)
            _istek += 1
            _sayfa_bekle(driver, timeout=12)
            items = driver.find_elements(By.CSS_SELECTOR, ".searchResultsItem")
            t_yukle = time.time() - t0

            # -- CHALLENGE: CF ise BEKLE, PX ise oturumu TERK et --
            _tur_tipi = challenge_turu(driver) if len(items) == 0 else None
            if _tur_tipi == "cf":
                # CF kendiliginden geciyor. Oturum saglam, terk ETME.
                print(f"[TUR {tur}] [~] CLOUDFLARE — kendiliginden gecmesi "
                      f"bekleniyor (tiklama YOK). Baslik="
                      f"{(driver.title or '')[:40]}")
                if cf_bekle(driver, 90):
                    items = driver.find_elements(By.CSS_SELECTOR,
                                                 ".searchResultsItem")
                    print(f"[TUR {tur}] [+] CF gecildi, {len(items)} ilan "
                          f"gorunuyor — oturum korundu")
                else:
                    print(f"[TUR {tur}] [!] CF 90 sn'de gecmedi — tur atlaniyor")
                    time.sleep(random.uniform(TEMEL_MIN, TEMEL_MAX))
                    continue
            elif _tur_tipi == "px":
                mola = CHALLENGE_MOLA[min(challenge_seri, len(CHALLENGE_MOLA) - 1)]
                challenge_seri += 1
                if CDP_PORT:
                    # Attach modunda kimlik degistirme imkani YOK (tarayici
                    # kullanicinin). Tek yapilabilen: beklemek.
                    print(f"[TUR {tur}] [!] PX BLOCK ({challenge_seri}. ust uste) "
                          f"— attach modunda oturum degistirilemez, "
                          f"{mola // 60} dk bekleniyor")
                    time.sleep(mola)
                    temkinli_kalan = TEMKINLI_TUR
                    continue
                print(f"[TUR {tur}] [!] PX BLOCK ({challenge_seri}. ust uste) — "
                      f"oturum TERK ediliyor, {mola // 60} dk mola. "
                      f"Baslik={(driver.title or '')[:40]}")
                print("           NOT: cozulmeye calisilmiyor — damgali "
                      "oturumda her istek damgayi tazeler.")
                surucu_kapat(driver)
                time.sleep(mola)
                driver = surucu_olustur(sessiz=True)
                oturum_basla = time.monotonic()
                gorulmus.clear()
                temkinli_kalan = TEMKINLI_TUR
                continue
            if len(items) == 0:
                print(f"[TUR {tur}] [!] 0 ilan (challenge DEGIL) | "
                      f"baslik={(driver.title or '')[:50]}")
            else:
                challenge_seri = 0

            global _SU_SEVIYESI
            _onceki_su = _SU_SEVIYESI
            yeni, guncel, en_yeni_id, taze = sayfa_isle(items, _onceki_su)
            if en_yeni_id > _SU_SEVIYESI:
                _SU_SEVIYESI = en_yeni_id

            # PARTI DUSTU MU? Sayfanin buyuk kismi yeniyse parti gelmis
            # demektir ve tasan kisim derin sayfalarda kaliyor.
            derin_yeni = derin_sayfa = 0
            # 06.10.2026 — 1. TURDA PARTI ALGISI YOK. Oturum yeni oldugu icin
            # sayfadaki her sey "yeni" gorunur (gorulmus seti bos); bot bunu
            # parti sanip derin sayfalara iniyordu: bosa 2 istek, hem de
            # oturumun EN HASSAS aninda. Parti ancak bir onceki turla
            # karsilastirilarak anlasilir.
            # Parti = TAZE ilan sayisi sayfayi doldurmaya yaklastiysa.
            # (Ilk turda su seviyesi henuz 0 oldugu icin atlanir.)
            _parti_esigi = max(PARTI_ESIK, int(PARTI_ORAN * len(items))) if items else 0
            if (PARTI_DUYARLI and tur > 1 and items and _onceki_su
                    and taze >= _parti_esigi):
                print(f"[TUR {tur}] PARTI ALGILANDI ({taze}/{len(items)} TAZE) "
                      f"— derin sayfalar okunuyor")
                derin_yeni, derin_sayfa = derin_sayfalari_oku(driver, tur)
                yeni += derin_yeni

            # ADAPTIF TEMPO
            if items:
                bos_tur = 0 if yeni else bos_tur + 1
            if temkinli_kalan > 0:
                temkinli_kalan -= 1
            hedef = hedef_periyot(bos_tur, temkinli_kalan)
            gecen = time.monotonic() - tur_basla
            mola = max(3, hedef - gecen)

            with db_baglan() as con:
                toplam = con.execute("SELECT COUNT(*) FROM oto_ilan").fetchone()[0]
            etiket = ""
            if temkinli_kalan > 0:
                etiket += f" temkinli({temkinli_kalan})"
            if bos_tur:
                etiket += f" bos({bos_tur})"
            # KAPSAMA UYARISI: sayfa bir turda neredeyse tamamen yenilenmisse
            # tur arasinda ilan KACIRIYOR olabilirsin. Periyodu kisaltmak
            # gerekir ama bu PX riskini artirir — karar kullanicinin.
            if derin_sayfa:
                etiket += f" +derin({derin_sayfa} sayfa, {derin_yeni} ilan)"
            print(f"[TUR {tur}] {datetime.now():%H:%M:%S} ilan={len(items)} "
                  f"taze={taze} yeni={yeni} guncel={guncel} en_yeni_id={en_yeni_id} "
                  f"db_toplam={toplam} istek={_istek} | "
                  f"yukle={t_yukle:.2f}s + mola={mola:.0f}s{etiket}")
            if not (MAX_TUR and tur >= MAX_TUR):   # son turda bosuna bekleme
                # Molanin bir kismini sayfa uzerinde insan gibi gecir;
                # kalanini bekle. Toplam tur periyodu DEGISMEZ.
                davranis_penceresi = min(mola * 0.5, 18.0)
                harcanan = insan_gibi_davran(driver, davranis_penceresi)
                time.sleep(max(0.0, mola - harcanan))

        except Exception as e:
            import traceback
            print(f"[TUR {tur}] [HATA] {e}")
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    pusuya_yat()
