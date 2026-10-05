"""
PX OLCUM ALETI — bir URL parametresi guvenli mi, TEK ISTEKLE olcer.

NEDEN AYRI ALET
  05.10.2026'da `pagingSize=50` botun icinde denendi ve TEK ISTEKTE IP'yi
  yakti (hard block, taze profil bile kurtarmadi). Ders: parametre denemesi
  asla tarama dongusunun icinde yapilmaz. Bu alet tam bir istek atar,
  hukmunu verir, cikar.

DISIPLIN ALETTE GOMULU
  - TEK istek (dongu yok, retry yok)
  - Her calismada TEMIZ anonim profil (damga tasimaz)
  - Son olcumden MIN_ARA dakika gecmeden calismayi REDDEDER (--zorla ile gecilir)
  - Sonucu px_olcum.log'a yazar (tarih, url, hukum, ilan sayisi)

KULLANIM
  py -3.12 arac_px_olcum.py                      # kontrol: duz URL
  py -3.12 arac_px_olcum.py "&pagingOffset=20"   # parametreyi dene
  py -3.12 arac_px_olcum.py --yol /otomobil/2    # path varyanti dene
  py -3.12 arac_px_olcum.py ... --zorla          # bekleme kuralini atla

HUKUMLER
  TEMIZ     : ilan geldi, challenge yok  -> parametre guvenli (bu oturumda)
  PX_BLOCK  : "Access to this page has been denied" -> PARAMETRE YANIK
  CF        : Cloudflare/basili tut ekrani -> belirsiz, tekrar et
  BOS       : challenge yok ama ilan da yok -> selektor/URL hatasi olabilir
"""
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

ROOT       = Path(__file__).parent
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
LOG        = ROOT / "px_olcum.log"
ZAMAN      = ROOT / ".px_olcum_son"     # son olcum zaman damgasi
MIN_ARA_DK = float(os.getenv("MIN_ARA_DK", "20"))

TEMEL = "https://www.sahibinden.com"
YOL   = "/otomobil"
BASE_PARAM = "?sorting=date_desc"

uc.Chrome.__del__ = lambda self: None

PX_ISARET = ("access to this page has been denied", "px-captcha",
             "sorry, you have been blocked", "erisim engellendi")
CF_ISARET = ("just a moment", "bir dakika", "checking your browser",
             "basili tut", "basılı tut", "cloudflare", "verify you are human",
             "bağlantınız kontrol")


def _brave_major():
    app = r"C:\Program Files\BraveSoftware\Brave-Browser\Application"
    try:
        v = [int(re.match(r"^(\d+)\.", d).group(1)) for d in os.listdir(app)
             if re.match(r"^\d+\.\d+\.\d+\.\d+$", d)]
        return max(v) if v else 154
    except Exception:
        return 154


def bekleme_kontrol(zorla):
    if zorla or not ZAMAN.exists():
        return True
    try:
        gecen = (time.time() - ZAMAN.stat().st_mtime) / 60.0
    except OSError:
        return True
    if gecen < MIN_ARA_DK:
        kalan = MIN_ARA_DK - gecen
        print(f"[DUR] Son olcumden {gecen:.1f} dk gecti. En az {MIN_ARA_DK:.0f} dk "
              f"beklenmeli — {kalan:.1f} dk daha.")
        print("      Sebep: ust uste istek, olculen seyi degil IP'nin sinirini olcer.")
        print("      Bilincli gecmek icin: --zorla")
        return False
    return True


def main():
    argv = [a for a in sys.argv[1:]]
    zorla = "--zorla" in argv
    argv = [a for a in argv if a != "--zorla"]
    yol = YOL
    if "--yol" in argv:
        i = argv.index("--yol")
        yol = argv[i + 1]
        del argv[i:i + 2]
    ek = argv[0] if argv else ""
    url = f"{TEMEL}{yol}{BASE_PARAM}{ek}&_={int(time.time() * 1000)}"

    print(f"OLCUM  : {url}")
    print(f"PROFIL : anonim (temiz gecici)")
    if not bekleme_kontrol(zorla):
        sys.exit(3)

    o = uc.ChromeOptions()
    o.add_argument("--disable-dev-shm-usage")
    o.page_load_strategy = "eager"
    d = uc.Chrome(options=o, browser_executable_path=BRAVE_PATH,
                  version_main=_brave_major(), no_sandbox=False)
    hukum, n, baslik = "HATA", 0, ""
    try:
        d.execute_cdp_cmd("Network.enable", {})
        d.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
        ZAMAN.write_text(str(time.time()), encoding="utf-8")   # istek ATILMADAN once
        t0 = time.time()
        d.get(url)                                              # <<< TEK ISTEK
        try:
            WebDriverWait(d, 15).until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".searchResultsItem")))
        except Exception:
            pass
        n = len(d.find_elements(By.CSS_SELECTOR, ".searchResultsItem"))
        baslik = (d.title or "")[:60]
        govde = ""
        try:
            govde = d.page_source[:8000].lower()
        except Exception:
            pass
        imza = (baslik.lower() + " " + govde)
        if any(k in imza for k in PX_ISARET):
            hukum = "PX_BLOCK"
        elif any(k in imza for k in CF_ISARET):
            hukum = "CF"
        elif n > 0:
            hukum = "TEMIZ"
        else:
            hukum = "BOS"
        print(f"SONUC  : {hukum}  ilan={n}  sure={time.time() - t0:.2f}s")
        print(f"BASLIK : {baslik}")
    finally:
        try:
            d.quit()
        except Exception:
            pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{hukum}\t"
                    f"ilan={n}\t{url}\t{baslik}\n")
    except OSError:
        pass
    if hukum == "PX_BLOCK":
        print("\n>>> PARAMETRE YANIK. Bu parametre bir daha kullanilmaz.")
        print(">>> IP damgali: en az 30-60 dk sahibinden'e HIC istek atma.")
    elif hukum == "TEMIZ":
        print("\n>>> Bu oturumda guvenli gorundu. Kalici hukum icin farkli")
        print(">>> saatlerde 2-3 kez tekrarla (tek istek, 20+ dk arayla).")
    sys.exit(0 if hukum in ("TEMIZ",) else 1)


if __name__ == "__main__":
    main()
