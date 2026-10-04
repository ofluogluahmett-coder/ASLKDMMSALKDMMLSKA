"""
OTOMOBIL TARAMA BOTU (sade)
PC bilesenleri botunun (sahibinden_bot.py) TARAMA cekirdeginin birebir
otomobil surumu. SADECE tarar + DB'ye yazar. Kelepir/Telegram YOK.

Calistirma:  py -3.12 oto_tarama.py
DB:          oto_tarama.db  (tablo: oto_ilan)
"""
import os
import re
import time
import random
import sqlite3
import undetected_chromedriver as uc
from datetime import datetime
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

ROOT       = Path(__file__).parent
DB_FILE    = ROOT / "oto_tarama.db"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

# PC botunda ANA_URL neydi, burada otomobil linki o.
ANA_URL = "https://www.sahibinden.com/otomobil?sorting=date_desc"

# Kac tur taranacak? 0 = sinirsiz (surekli calis).
MAX_TUR = int(os.getenv("MAX_TUR", "10"))

# HIZ: gorsel/font/tracker byte'larini engelle (ilan verisi DOM'da kalir).
BLOCK_URLS = [
    "*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.svg",
    "*shbdn.com/photos*", "*.woff", "*.woff2", "*.ttf",
    "*google-analytics*", "*googletagmanager*", "*doubleclick*",
    "*facebook.net*", "*hotjar*", "*criteo*",
]

gorulmus = set()   # bu oturumda islenen data-id'ler

# uc'nin kapanista cift quit() denemesi "Chrome.__del__ / WinError 6" traceback'i
# basiyor (is bittikten SONRA, zararsiz ama crash gibi gorunuyor). Susturuyoruz.
uc.Chrome.__del__ = lambda self: None


# ── DB ──────────────────────────────────────────────────────────────────────
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


# ── Tarayici yardimcilari (PC botundan birebir) ─────────────────────────────
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


def surucu_olustur():
    options = uc.ChromeOptions()
    options.add_argument("--disable-dev-shm-usage")
    options.page_load_strategy = "eager"

    surum = _brave_major_version()
    print(f"Brave major surum: {surum}")

    # ANONIM MOD: user_data_dir YOK -> her acilista temiz gecici profil.
    # (PC botunda CF 'basili tut' spam'inin cozumu tam buydu: kalici profil damgalaniyor.)
    print("Anonim mod: temiz gecici profil (kalici profil damgasi yok).")
    driver = uc.Chrome(
        options=options,
        browser_executable_path=BRAVE_PATH,
        version_main=surum,
        no_sandbox=False,
    )
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": BLOCK_URLS})
        driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
        print(f"CDP resource-blocking + cache-disabled aktif ({len(BLOCK_URLS)} desen).")
    except Exception as e:
        print(f"[UYARI] CDP setup kurulamadi: {e}")

    # PX GEC ENTEGRASYONU: uc'nin sectigi gercek debug portunu dosyaya yaz.
    # px_gec.py bu dosyadan okuyup ayni tarayiciya baglanir (port sabitlemeye gerek yok).
    try:
        adres = driver.capabilities["goog:chromeOptions"]["debuggerAddress"]  # "127.0.0.1:PORT"
        port = adres.split(":")[-1]
        (ROOT / "cdp_port.txt").write_text(port, encoding="utf-8")
        print(f"CDP debug adresi: {adres}  (cdp_port.txt yazildi -> px_gec.py bunu okur)")
    except Exception as e:
        print(f"[UYARI] debug portu okunamadi: {e}")
    return driver


# ── Ana dongu ───────────────────────────────────────────────────────────────
CF_ISARET = (
    "just a moment", "bir dakika", "dakika lutfen", "baglantiniz kontrol",
    "bağlantınız kontrol", "kontrol ediliyor", "basili tut", "basılı tut",
    "checking your browser", "attention required", "cloudflare",
    "access to this page has been denied", "erisim engellendi",
    "sorry, you have been blocked", "verify you are human",
)


def pusuya_yat():
    db_hazirla()
    driver = surucu_olustur()
    limit_yazi = f"{MAX_TUR} tur" if MAX_TUR else "sinirsiz"
    print(f"OTO TARAMA basladi. Tur periyodu: ~50-75 sn | Limit: {limit_yazi}\nURL: {ANA_URL}")

    tur = 0
    while True:
        if MAX_TUR and tur >= MAX_TUR:
            with db_baglan() as con:
                toplam = con.execute("SELECT COUNT(*) FROM oto_ilan").fetchone()[0]
            print(f"\n=== {MAX_TUR} TUR BITTI === DB'deki toplam ilan: {toplam} "
                  f"({DB_FILE.name})")
            try:
                driver.quit()
            except Exception:
                pass
            return
        tur += 1
        tur_basla = time.monotonic()
        try:
            # Cache-buster: her tur farkli URL -> origin taze cevaba zorlanir
            t0 = time.time()
            driver.get(f"{ANA_URL}&_={int(time.time() * 1000)}")
            _sayfa_bekle(driver, timeout=12)
            items = driver.find_elements(By.CSS_SELECTOR, ".searchResultsItem")
            t_yukle = time.time() - t0

            # CF / challenge tespiti
            if len(items) == 0:
                govde = ""
                try:
                    govde = driver.page_source[:6000].lower()
                except Exception:
                    pass
                basl = (driver.title or "").lower()
                if any(k in basl for k in CF_ISARET) or any(k in govde for k in CF_ISARET):
                    print(f"[TUR {tur}] [!] CLOUDFLARE/PX EKRANI — 60sn bekliyorum "
                          f"(o pencerede 'basili tut')")
                    time.sleep(60)
                    continue
                print(f"[TUR {tur}] [!] 0 ilan | baslik={driver.title[:60]}")

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
                            By.CSS_SELECTOR, ".searchResultsTitleValue a").get_attribute("href")
                    except Exception:
                        href = None
                    url = href or f"https://www.sahibinden.com/ilan/{ilan_id}/detay"

                    # Gorsel ALINMIYOR (istege gore) — zaten CDP ile byte'lari da bloklu.
                    if ilan_kaydet(ilan_id, baslik, fiyat, yil, km, url):
                        yeni += 1
                    else:
                        guncel += 1
                    gorulmus.add(ilan_id)
                except Exception:
                    continue

            # Tur periyodunu sabitle: toplam (tarama+mola) 50-75 sn
            hedef = random.uniform(50, 75)
            gecen = time.monotonic() - tur_basla
            mola = max(3, hedef - gecen)
            with db_baglan() as con:
                toplam = con.execute("SELECT COUNT(*) FROM oto_ilan").fetchone()[0]
            print(f"[TUR {tur}] {datetime.now():%H:%M:%S} ilan={len(items)} "
                  f"yeni={yeni} guncel={guncel} en_yeni_id={en_yeni_id} "
                  f"db_toplam={toplam} | yukle={t_yukle:.2f}s + mola={mola:.0f}s")
            if not (MAX_TUR and tur >= MAX_TUR):   # son turda bosuna bekleme
                time.sleep(mola)

        except Exception as e:
            import traceback
            print(f"[TUR {tur}] [HATA] {e}")
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    pusuya_yat()
