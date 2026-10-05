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
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

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
GUVENLI_PARAMETRELER = {"sorting", "_"}
PARAMETRE_KONTROL = os.getenv("PARAMETRE_KONTROL", "1") != "0"

# --- 2) ADAPTIF TEMPO ----------------------------------------------------
# Taban periyot: turun TOPLAM suresi (tarama+mola) bu hedefte tutulur.
TEMEL_MIN = float(os.getenv("TEMEL_MIN", "50"))
TEMEL_MAX = float(os.getenv("TEMEL_MAX", "75"))
PERIYOT_TAVANI = 420.0
# Gece ilan akisi durur -> ayni tempoda taramak bedava risktir.
GECE_BASLA, GECE_BITIS = 2, 7
GECE_CARPANI = float(os.getenv("GECE_CARPANI", "2.5"))
# Ust uste bos tur (yeni ilan yok) -> kategori sogumus, yavasla.
BOS_TUR_CARPAN = 1.35
BOS_TUR_TAVAN  = 4          # carpan en fazla 1.35^4

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

# HIZ: gorsel/font/tracker byte'larini engelle (ilan verisi DOM'da kalir).
# GORSEL_BLOK=0 ile kapatilabilir — "hic gorsel cekmeyen istemci" imzasi
# PX'e anormal gorunuyor mu? henuz olculmedi, test icin acik kapi.
GORSEL_BLOK = os.getenv("GORSEL_BLOK", "1") != "0"
BLOCK_URLS = [
    "*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.svg",
    "*shbdn.com/photos*", "*.woff", "*.woff2", "*.ttf",
    "*google-analytics*", "*googletagmanager*", "*doubleclick*",
    "*facebook.net*", "*hotjar*", "*criteo*",
]

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
    app = r"C:\Program Files\BraveSoftware\Brave-Browser\Application"
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


def surucu_olustur(sessiz=False):
    options = uc.ChromeOptions()
    options.add_argument("--disable-dev-shm-usage")
    options.page_load_strategy = "eager"

    surum = _brave_major_version()
    if not sessiz:
        print(f"Brave major surum: {surum}")
        # ANONIM MOD: user_data_dir YOK -> her acilista temiz gecici profil.
        # (PC botunda CF 'basili tut' spam'inin cozumu tam buydu.)
        print("Anonim mod: temiz gecici profil (kalici profil damgasi yok).")
    driver = uc.Chrome(
        options=options,
        browser_executable_path=BRAVE_PATH,
        version_main=surum,
        no_sandbox=False,
    )
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        if GORSEL_BLOK:
            driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": BLOCK_URLS})
        driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
        if not sessiz:
            print("CDP: cache-disabled + gorsel_blok="
                  f"{'acik' if GORSEL_BLOK else 'KAPALI'}")
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
    return driver


def surucu_kapat(driver):
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


def challenge_mi(driver):
    basl = (driver.title or "").lower()
    if any(k in basl for k in CF_ISARET):
        return True
    try:
        return any(k in driver.page_source[:6000].lower() for k in CF_ISARET)
    except Exception:
        return False


def hedef_periyot(bos_tur, temkinli_kalan):
    """Turun TOPLAM suresi icin hedef (sn). Risk arttikca uzar."""
    p = random.uniform(TEMEL_MIN, TEMEL_MAX)
    if bos_tur:
        p *= BOS_TUR_CARPAN ** min(bos_tur, BOS_TUR_TAVAN)
    if temkinli_kalan > 0:
        p *= TEMKINLI_CARPAN
    if GECE_BASLA <= datetime.now().hour < GECE_BITIS:
        p *= GECE_CARPANI
    return min(p, PERIYOT_TAVANI)


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
    print(f"URL: {ANA_URL}")

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
            # Cache-buster: her tur farkli URL -> origin taze cevaba zorlanir
            t0 = time.time()
            driver.get(f"{ANA_URL}&_={int(time.time() * 1000)}")
            _istek += 1
            _sayfa_bekle(driver, timeout=12)
            items = driver.find_elements(By.CSS_SELECTOR, ".searchResultsItem")
            t_yukle = time.time() - t0

            # -- CHALLENGE: oturumu TERK et, katlanarak bekle, temiz don --
            if len(items) == 0 and challenge_mi(driver):
                mola = CHALLENGE_MOLA[min(challenge_seri, len(CHALLENGE_MOLA) - 1)]
                challenge_seri += 1
                print(f"[TUR {tur}] [!] CHALLENGE ({challenge_seri}. ust uste) — "
                      f"oturum TERK ediliyor, {mola // 60} dk mola. "
                      f"Baslik={(driver.title or '')[:40]}")
                print("           NOT: challenge COZULMEYE calisilmiyor — damgali "
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

            yeni = 0
            guncel = 0
            en_yeni_id = 0
            for item in items:
                try:
                    ilan_id = item.get_attribute("data-id")
                    if not ilan_id or not ilan_id.isdigit():
                        continue
                    en_yeni_id = max(en_yeni_id, int(ilan_id))
                    if ilan_id in gorulmus:
                        continue

                    baslik = _metin(item, ".searchResultsTitleValue")
                    fiyat_metin = _metin(item, ".searchResultsPriceValue")
                    fiyat_s = re.sub(r"[^\d]", "", fiyat_metin)
                    if not baslik or not fiyat_s:
                        continue
                    fiyat = float(fiyat_s)

                    # Otomobilde kartta yil + km kolonlari var
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

                    # Link: karttan al, olmazsa ID'den kur
                    try:
                        href = item.find_element(
                            By.CSS_SELECTOR,
                            ".searchResultsTitleValue a").get_attribute("href")
                    except Exception:
                        href = None
                    url = href or f"https://www.sahibinden.com/ilan/{ilan_id}/detay"

                    # Gorsel ALINMIYOR (istege gore).
                    if ilan_kaydet(ilan_id, baslik, fiyat, yil, km, url):
                        yeni += 1
                    else:
                        guncel += 1
                    gorulmus.add(ilan_id)
                except Exception:
                    continue

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
            if items and yeni >= len(items) - 1:
                etiket += " [sayfa tam dondu: ilan kaciriyor olabilirsin]"
            print(f"[TUR {tur}] {datetime.now():%H:%M:%S} ilan={len(items)} "
                  f"yeni={yeni} guncel={guncel} en_yeni_id={en_yeni_id} "
                  f"db_toplam={toplam} istek={_istek} | "
                  f"yukle={t_yukle:.2f}s + mola={mola:.0f}s{etiket}")
            if not (MAX_TUR and tur >= MAX_TUR):   # son turda bosuna bekleme
                time.sleep(mola)

        except Exception as e:
            import traceback
            print(f"[TUR {tur}] [HATA] {e}")
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    pusuya_yat()
