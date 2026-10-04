"""OTO KELEPIR AVCISI — BAGIMSIZ KOHORT TAKIP CALISTIRICISI.

NEDEN AYRI PROCESS
  Takip botun ICINDE calisiyordu. 23-24.09 gecesi bot 01:44'te oldu ve
  10.5 saat kapali kaldi — takip de olduyle birlikte olurdu. Kontrol
  gunleri (2/4/7/11/15) botun ayakta olmasina bagli olamaz, yoksa olcum
  penceresi kayar ve kohort bozulur.

  Kazanc: IZOLASYON ve SUREKLILIK. Gizlenme DEGIL — ayni IP, ayni profil,
  ayni hesap. Toplam istek sayisi degismiyor, sadece botun kaderine
  bagimli olmaktan cikiyor.

NEDEN AYNI ANDA CALISAMAZ (ve bu neden iyi)
  Chrome/Brave user-data-dir'i kilitler; bot ve runner ayni login
  profilini paylastigi icin ikisi ayni anda acilamaz. Bu teknik kisit
  isimize yariyor: tek IP'den ES ZAMANLI iki oturum, tek oturumdan daha
  anormal bir desendir. Runner basta botu arar, calisiyorsa HIC baslamaz.

KULLANIM
  python takip_runner.py            # sirasi gelenleri kontrol et
  python takip_runner.py --kohort   # once yeni kohort ekle, sonra kontrol
  python takip_runner.py --zorla    # bot calisiyor olsa bile dene (TAVSIYE EDILMEZ)
  python takip_runner.py --kuru     # ag yok: sadece ne yapacagini goster
"""

import argparse
import logging
import os
import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "oto_hafiza.db"
LOG_FILE = ROOT / "takip.log"

# ── AYARLAR ───────────────────────────────────────────────
TUR_BASINA_TAVAN = int(os.getenv("TAKIP_RUN_TAVAN", "30") or "30")
BEKLEME_MIN = float(os.getenv("TAKIP_RUN_BEKLEME_MIN", "30") or "30")
BEKLEME_MAX = float(os.getenv("TAKIP_RUN_BEKLEME_MAX", "60") or "60")
SAYFA_TAVANI_SN = 45
ARDARDA_BELIRSIZ_TAVANI = 3      # ust uste bu kadar belirsiz → dur


def _log_kur():
    lg = logging.getLogger("TAKIP")
    if lg.handlers:
        return lg
    lg.setLevel(logging.INFO)
    bicim = logging.Formatter("%(asctime)s [TAKIP] %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(bicim)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(bicim)
    lg.addHandler(fh)
    lg.addHandler(sh)
    return lg


log = _log_kur()


# ── BOT CALISIYOR MU ──────────────────────────────────────
def bot_calisiyor_mu():
    """oto_bot.py calistiran bir python process'i var mi?
    Donus: (calisiyor_mu, pid veya None)"""
    try:
        import psutil
    except ImportError:
        log.warning("psutil yok — bot kontrolu YAPILAMADI. Profil kilidi "
                    "yuzunden driver acilmazsa sebebi budur.")
        return False, None
    benim = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == benim:
                continue
            cmd = " ".join(p.info["cmdline"] or [])
            if "oto_bot.py" in cmd:
                return True, p.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False, None


# ── KONTROL ───────────────────────────────────────────────
def _getir(driver, url):
    """Sayfayi ac, (html, title) dondur. Hata olursa (None, None)."""
    from selenium.common.exceptions import TimeoutException
    try:
        driver.set_page_load_timeout(SAYFA_TAVANI_SN)
    except Exception:
        pass
    try:
        driver.get(url)
    except TimeoutException:
        pass          # yuklenmis olabilir, asagida kontrol edilir
    except Exception as e:
        log.warning(f"  navigasyon hatasi: {type(e).__name__}")
        return None, None
    time.sleep(random.uniform(2.5, 4.0))
    try:
        return driver.page_source, (driver.title or "")
    except Exception:
        return None, None


def kontrol_et(driver, con, kuyruk, kuru=False):
    import takip
    sayac = {"canli": 0, "gitti": 0, "block": 0, "belirsiz": 0}
    ardarda_belirsiz = 0

    for i, (iid, kohort, url) in enumerate(kuyruk):
        if not url:
            continue
        if kuru:
            log.info(f"  [KURU] {kohort:<8} {iid}")
            continue

        html, title = _getir(driver, url)
        durum = takip.sayfa_durumu(html, title)
        takip.sonuc_yaz(con, iid, durum)
        sayac[durum] = sayac.get(durum, 0) + 1

        if durum == "gitti":
            log.info(f"  {kohort:<8} {iid} → YAYINDAN KALKTI")
            ardarda_belirsiz = 0
        elif durum == "canli":
            log.info(f"  {kohort:<8} {iid} → hala yayinda")
            ardarda_belirsiz = 0
        else:
            log.warning(f"  {kohort:<8} {iid} → {durum.upper()} "
                        f"(SAYIMA GIRMEDI)")
            ardarda_belirsiz += 1
            if durum == "block":
                log.warning("BLOCK sinyali — bu calistirma DURDURULUYOR. "
                            "Kalanlar bir sonraki calistirmada denenecek.")
                break
            if ardarda_belirsiz >= ARDARDA_BELIRSIZ_TAVANI:
                log.warning(f"{ardarda_belirsiz} ust uste belirsiz — "
                            f"durduruluyor (sayfa yapisi degismis olabilir, "
                            f"takip.py CANLI_ISARET/GITTI_ISARET'e bak)")
                break

        if i < len(kuyruk) - 1:
            bekle = random.uniform(BEKLEME_MIN, BEKLEME_MAX)
            log.info(f"  ... {bekle:.0f}sn")
            time.sleep(bekle)

    return sayac


def main():
    ap = argparse.ArgumentParser(description="Kohort takip calistiricisi")
    ap.add_argument("--kohort", action="store_true",
                    help="once yeni adaylari takibe al")
    ap.add_argument("--zorla", action="store_true",
                    help="bot calisiyor olsa bile dene (profil kilidi patlayabilir)")
    ap.add_argument("--kuru", action="store_true",
                    help="ag kullanma, sadece ne yapilacagini goster")
    ap.add_argument("--tavan", type=int, default=TUR_BASINA_TAVAN,
                    help=f"bu calistirmada en fazla kac kontrol (varsayilan {TUR_BASINA_TAVAN})")
    ap.add_argument("--db", default=str(DB_FILE),
                    help="farkli bir veritabani (prova/test icin kopya)")
    a = ap.parse_args()

    log.info("=" * 58)
    log.info(f"kohort takip calistiricisi — {datetime.now():%Y-%m-%d %H:%M}")

    calisiyor, pid = bot_calisiyor_mu()
    if calisiyor and not a.zorla and not a.kuru:
        log.warning(f"BOT CALISIYOR (pid {pid}) — runner BASLAMIYOR.")
        log.warning("Sebep: bot ve runner ayni login profilini paylasiyor, "
                    "Chrome profili kilitler. Ayrica tek IP'den es zamanli "
                    "iki oturum istenmeyen bir desen.")
        log.warning("Botu durdurup tekrar dene, ya da --zorla kullan.")
        return 2
    if calisiyor and a.kuru:
        log.info(f"(bot calisiyor, pid {pid} — kuru calistirma etkilenmez)")

    if a.db != str(DB_FILE):
        log.warning(f"FARKLI VERITABANI: {a.db}  (prova modu)")
    con = sqlite3.connect(a.db, timeout=30.0)
    try:
        import takip

        if a.kohort:
            import kelepir
            rows, modeller, skorlar, adaylar = kelepir.calistir()
            ek_a, ek_k = takip.kohort_ekle(con, adaylar, skorlar)
            log.info(f"kohorta eklendi: {ek_a} aday + {ek_k} eslestirilmis "
                     f"kontrol  (havuzda {len(adaylar)} aday)")

        kuyruk = takip.kontrol_kuyrugu(con, tavan=a.tavan)
        if not kuyruk:
            log.info("sirasi gelen ilan YOK — kontrol gunleri: "
                     f"{takip.KONTROL_GUNLERI}")
            log.info(takip.rapor(con))
            return 0

        n_aday = sum(1 for _, k, _ in kuyruk if k == "aday")
        log.info(f"kontrol edilecek: {len(kuyruk)} ilan "
                 f"({n_aday} aday, {len(kuyruk)-n_aday} kontrol)")
        sure = len(kuyruk) * (BEKLEME_MIN + BEKLEME_MAX) / 2 / 60
        log.info(f"tahmini sure: ~{sure:.0f} dakika")

        driver = None
        if not a.kuru:
            log.info("Brave aciliyor (botun login profili)...")
            import oto_bot
            driver = oto_bot._driver_olustur()
            log.info("tarayici hazir")

        try:
            sayac = kontrol_et(driver, con, kuyruk, kuru=a.kuru)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
                log.info("tarayici kapatildi")

        if not a.kuru:
            log.info(f"SONUC: canli={sayac['canli']} gitti={sayac['gitti']} "
                     f"block={sayac['block']} belirsiz={sayac['belirsiz']}")
        log.info("\n" + takip.rapor(con))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
