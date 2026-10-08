"""
PC KÖPRÜSÜ — uzantinin topladigi PC BILESENI sayfasini apex_predator'un
KANITLANMIS motorundan gecirip kelepir_hafiza.db'ye yazar.

NEDEN BOYLE
  08.10.2026'da otomobil kategorisinde su kanitlandi: tarama tarayicinin
  ICINDEN yapilinca PX yok (165 dakikada 1, o da bizim hatamizdan) ve
  "_=<ms>" ile sunucu onbellegi kirilinca tur basina TAZE ilan 26 KAT
  artiyor. PC botu ise hala WebDriver ile suruluyor ve CLAUDE.md'de
  "CF basili tut spam'i" + "~3-5 dk gecikme" diye yazili ayni dertleri
  yasiyor. Ayni ilac muhtemelen oraya da uyuyor.

  Icerik betigi sahibinden sayfasindan calistigi icin AYNI ALAN
  ADINDAKI her kategoriyi XHR ile cekebilir. PC kategorisi KENDI
  sekmesinde kendi 50-80 sn'lik dongusuyle doner -> mevcut tempo
  birebir korunur, hiz KAYBEDILMEZ.

NE YAPAR
  1) HTML'i apex'in secicileriyle ayristirir (tr.searchResultsItem,
     .searchResultsPriceValue, .searchResultsTitleValue, thumbnail img)
  2) Her yeni ilani KelepirMotor.veri_kaydet() ile kelepir_hafiza.db'ye
     yazar — yani gereksiz/kombo/anomali filtreleri, normalizasyon,
     GPU tier, dinamik piyasa ortalamasi HEPSI calisir.
  3) kelepir_avci.py zaten fiyat_gecmisi'ni izliyor; oraya yazdigimiz an
     FIRSAT/VURGUN bildirimleri KENDILIGINDEN gider. Yani bildirim
     yolunu yeniden yazmiyoruz.
  4) Opsiyonel: scraper kanalina "yeni ilan" akisi (apex'in kendi
     TELEGRAM_TOKEN'i ile, botun formatiyla).

SINIR
  - apex'in sahibinden_bot.py MODULU ICE AKTARILMAZ (modul duzeyinde
    kuyruk/isci/driver kurulumu var; ice aktarmak bir botu daha
    baslatmak olur). Sadece kelepir_motor (temiz kutuphane) kullanilir.
  - Gorsel INDIRILMEZ; sadece URL metni saklanir/yollanir.
  - Bu modul sahibinden'e HICBIR istek atmaz.
"""
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

APEX = Path(r"C:\Users\AHMET1\Desktop\apex_predator")

_motor = None
_hata_yazildi = False
_kilit = threading.Lock()

# Scraper kanali (apex'in kendi botu) — "yeni ilan girdi" akisi
_tg_kuyruk = queue.Queue(maxsize=200)
_tg_isci_basladi = False
_sayac = {"yazilan": 0, "atlanan": 0, "tg_gonderildi": 0, "tg_hata": 0}


def _apex_ayar():
    """apex .env'inden scraper kanalinin token/chat'ini oku."""
    tok = cid = ""
    try:
        for sat in (APEX / ".env").read_text(encoding="utf-8-sig").splitlines():
            sat = sat.strip()
            if sat.startswith("TELEGRAM_TOKEN="):
                tok = sat.split("=", 1)[1].strip()
            elif sat.startswith("TELEGRAM_CHAT_ID="):
                cid = sat.split("=", 1)[1].strip()
    except Exception:
        pass
    return tok, cid


APEX_TOKEN, APEX_CHAT = _apex_ayar()
TG_ACIK = (os.getenv("PC_TG_BESLEME", "1") != "0") and bool(
    APEX_TOKEN and APEX_CHAT)


def motor():
    """apex'in KelepirMotor'unu tembel ice aktar."""
    global _motor, _hata_yazildi
    if _motor is not None:
        return _motor
    try:
        if str(APEX) not in sys.path:
            sys.path.insert(0, str(APEX))
        import kelepir_motor
        # kelepir_motor DB_FILE'i GORECELI tutuyor ("kelepir_hafiza.db").
        # Ilk cozumum os.chdir(APEX) idi ama cwd SURECE AITTIR ve sunucu
        # COK IS PARCACIKLI — otomobil hatti ayni anda calisirken dizini
        # degistirmek kutu bir yan etki olurdu. Neyse ki motorun TUM ic
        # cagrilari db_baglan(DB_FILE) seklinde, yani yolu CAGRI ANINDA
        # okuyor; modul sabitini mutlak yola cevirmek yeterli ve guvenli.
        # (Instantiate etmeden ONCE yapilmali: __init__ tablolari kuruyor.)
        kelepir_motor.DB_FILE = str(APEX / "kelepir_hafiza.db")
        _motor = kelepir_motor.KelepirMotor()
        print("  [pc_kopru] apex KelepirMotor ice aktarildi "
              f"(DB: {APEX / 'kelepir_hafiza.db'})")
    except Exception as e:
        _motor = False
        if not _hata_yazildi:
            print(f"  [pc_kopru] motor ice aktarilamadi, PC yazma KAPALI: "
                  f"{str(e)[:140]}")
            _hata_yazildi = True
    return _motor


# ── Ayristirma (apex'in secicileriyle, BeautifulSoup) ─────────────────
_RE_SAYI = re.compile(r"[^\d]")


def kartlari_ayristir(html, konsol=False):
    """Doner: [{id, baslik, fiyat, gorsel, url}, ...]

    konsol=True ise ilan linki oyun-konsolu kategorisine gore kurulur.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception as e:
        print(f"  [pc_kopru] bs4 yok: {e}")
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select("tr.searchResultsItem"):
        iid = (row.get("data-id") or "").strip()
        if not iid.isdigit():
            continue                      # nativeAd / reklam satirlari
        bas_el = row.select_one(".searchResultsTitleValue")
        fi_el = row.select_one("td.searchResultsPriceValue")
        if not bas_el or not fi_el:
            continue
        baslik = " ".join(bas_el.get_text(" ", strip=True).split())
        fiyat_t = _RE_SAYI.sub("", fi_el.get_text(strip=True))
        if not baslik or not fiyat_t:
            continue
        gorsel = ""
        img = row.select_one(".searchResultsLargeThumbnail img")
        if img:
            for alan in ("src", "data-src", "data-original"):
                v = img.get(alan) or ""
                if v.startswith("http"):
                    gorsel = v
                    break
        out.append({
            "id": iid, "baslik": baslik, "fiyat": float(fiyat_t),
            "gorsel": gorsel,
            # apex botunun urettigi link bicimi
            "url": (f"https://www.sahibinden.com/ilan/{iid}/detay"
                    if konsol else
                    "https://www.sahibinden.com/ilan/ikinci-el-ve-sifir-"
                    f"alisveris-bilgisayar-masaustu-ilan-{iid}/detay"),
        })
    return out


# ── Daha once gorulenler (tekrar yazmamak + tekrar bildirmemek) ──────
_GORULEN_DOSYA = Path(__file__).parent / "pc_gorulmus.json"
_gorulen = None


def _gorulen_yukle():
    global _gorulen
    if _gorulen is not None:
        return _gorulen
    try:
        _gorulen = set(json.loads(
            _GORULEN_DOSYA.read_text(encoding="utf-8")))
    except Exception:
        _gorulen = set()
    return _gorulen


def _gorulen_kaydet():
    try:
        # Son 5000 ile sinirla (dosya sismesin)
        g = list(_gorulen_yukle())[-5000:]
        _GORULEN_DOSYA.write_text(json.dumps(g), encoding="utf-8")
    except Exception:
        pass


# ── Scraper kanali (apex'in "yeni ilan" akisi) ───────────────────────
def _tg_post(metot, alan):
    veri = urllib.parse.urlencode(alan).encode("utf-8")
    istek = urllib.request.Request(
        f"https://api.telegram.org/bot{APEX_TOKEN}/{metot}", data=veri)
    with urllib.request.urlopen(istek, timeout=20) as c:
        return json.loads(c.read().decode("utf-8"))


def _tg_isci():
    while True:
        b, f, l, g = _tg_kuyruk.get()
        try:
            mesaj = (f"🖥 <b>{b[:110]}</b>\n{f:,.0f} TL".replace(",", ".")
                     + f"\n<a href=\"{l}\">ilana git</a>")
            ok = False
            if g:
                try:
                    ok = _tg_post("sendPhoto", {
                        "chat_id": APEX_CHAT, "photo": g,
                        "caption": mesaj, "parse_mode": "HTML"}).get("ok")
                except Exception:
                    ok = False
            if not ok:
                ok = _tg_post("sendMessage", {
                    "chat_id": APEX_CHAT, "text": mesaj,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true"}).get("ok")
            _sayac["tg_gonderildi" if ok else "tg_hata"] += 1
        except Exception as e:
            _sayac["tg_hata"] += 1
            print(f"  [pc_kopru tg] {str(e)[:90]}")
        finally:
            _tg_kuyruk.task_done()
        time.sleep(2.5)


def _tg_kuyruga(b, f, l, g):
    global _tg_isci_basladi
    if not TG_ACIK:
        return
    with _kilit:
        if not _tg_isci_basladi:
            threading.Thread(target=_tg_isci, daemon=True,
                             name="pc_tg").start()
            _tg_isci_basladi = True
    try:
        _tg_kuyruk.put_nowait((b, f, l, g))
    except queue.Full:
        try:
            _tg_kuyruk.get_nowait()
            _tg_kuyruk.put_nowait((b, f, l, g))
        except Exception:
            pass


# ── Ana giris ────────────────────────────────────────────────────────
def html_isle(html, tg_tavan=5):
    """PC bileseni liste HTML'ini isle.

    Doner: (yazilan, yeni_gorulen, toplam_kart)
    Hata halinde (0, 0, 0) — tarama akisi ASLA bozulmaz.
    """
    m = motor()
    if not m or not html:
        return 0, 0, 0
    try:
        kartlar = kartlari_ayristir(html)
    except Exception as e:
        print(f"  [pc_kopru] ayristirma hatasi: {str(e)[:120]}")
        return 0, 0, 0
    if not kartlar:
        return 0, 0, 0

    gorulen = _gorulen_yukle()
    yeni = [k for k in kartlar if k["id"] not in gorulen]
    yazilan = 0
    # DB yolu motor ice aktarilirken mutlaklastirildi; os.chdir YOK
    # (cwd surece ait ve sunucu cok is parcacikli — bkz. motor()).
    for k in yeni:
        try:
            m.veri_kaydet(k["baslik"], k["fiyat"], k["url"],
                          "sahibinden", k["gorsel"])
            yazilan += 1
        except Exception:
            _sayac["atlanan"] += 1

    # Scraper kanalina akis (tavanli)
    for k in yeni[:tg_tavan]:
        _tg_kuyruga(k["baslik"], k["fiyat"], k["url"], k["gorsel"])

    for k in kartlar:
        gorulen.add(k["id"])
    _gorulen_kaydet()
    _sayac["yazilan"] += yazilan
    return yazilan, len(yeni), len(kartlar)


# ── KONSOL KATEGORISI ────────────────────────────────────────────────
# apex botunun konsol_kaydet_db() + _konsol_temizle() + _istatistik_guncelle()
# mantigi BIREBIR tasindi. Neden kopyalandi: bu uc fonksiyon
# sahibinden_bot.py icinde ve o modul ice aktarilamaz (modul duzeyinde
# kuyruk/isci kurulumu var). Ama kullandiklari her sey (PREFIXLER,
# KONSOL_AKTIF, tani_konsol_modeli, KelepirMotor._kesik_ortalama)
# kelepir_motor'dan geliyor, yani FILTRE MANTIGI ayni kaynaktan.
#
# Korunan kurallar (29.08.2026 kararlari):
#   - fiyat bandi 8.000-120.000 TL (PS5/Series X bu bandin altinda olmaz)
#   - KONSOL_AKTIF beyaz listesi: sadece PS5 varyantlari + Xbox Series X/S
#   - model taninmazsa VEYA listede yoksa KAYDEDILMEZ
KONSOL_ALT, KONSOL_UST = 8000, 120000


def _konsol_temizle(baslik, prefixler):
    import unicodedata
    baslik = baslik.replace("̇", "")      # combining dot (İ -> i̇)
    temiz = unicodedata.normalize("NFC", baslik).lower().strip()
    temiz = "".join(c for c in temiz
                    if unicodedata.category(c) != "Mn")
    for p in prefixler:
        temiz = temiz.replace(p.lower(), " ")
    temiz = re.sub(r"\d[\d.,]*\s*tl\b", " ", temiz)
    temiz = re.sub(r"\d+\s*taksit", " ", temiz)
    return re.sub(r"\s+", " ", temiz).strip()


def _istatistik_guncelle(km, con, urun, tier):
    from datetime import datetime as _dt
    fiyatlar = [r[0] for r in con.execute(
        "SELECT fiyat FROM fiyat_gecmisi WHERE urun=? AND tier=?",
        (urun, tier)).fetchall()]
    if not fiyatlar:
        return
    ort = round(km.KelepirMotor._kesik_ortalama(fiyatlar), 0)
    con.execute("""
        INSERT INTO urun_istatistik
            (urun_tier, urun, tier, ortalama, min_fiyat, max_fiyat,
             ilan_sayisi, guncellendi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(urun_tier) DO UPDATE SET
            ortalama=excluded.ortalama, min_fiyat=excluded.min_fiyat,
            max_fiyat=excluded.max_fiyat,
            ilan_sayisi=excluded.ilan_sayisi,
            guncellendi=excluded.guncellendi
    """, (f"{urun}__t{tier}", urun, tier, ort, min(fiyatlar),
          max(fiyatlar), len(fiyatlar),
          _dt.now().isoformat(timespec="seconds")))


def konsol_isle(html):
    """Konsol liste HTML'ini isle. Doner: (yazilan, yeni, toplam)."""
    if not motor() or not html:
        return 0, 0, 0
    import kelepir_motor as km
    try:
        kartlar = kartlari_ayristir(html, konsol=True)
    except Exception as e:
        print(f"  [pc_kopru konsol] ayristirma: {str(e)[:110]}")
        return 0, 0, 0
    if not kartlar:
        return 0, 0, 0

    gorulen = _gorulen_yukle()
    yeni = [k for k in kartlar if ("k" + k["id"]) not in gorulen]
    yazilan = 0
    try:
        con = km.db_baglan(str(APEX / "kelepir_hafiza.db"))
    except Exception as e:
        print(f"  [pc_kopru konsol] DB: {str(e)[:110]}")
        return 0, 0, len(kartlar)
    try:
        for k in yeni:
            f = k["fiyat"]
            if f < KONSOL_ALT or f > KONSOL_UST:
                continue
            temiz = _konsol_temizle(k["baslik"], km.PREFIXLER)[:100]
            if not temiz:
                continue
            model = km.tani_konsol_modeli(temiz)
            if not model or model not in km.KONSOL_AKTIF:
                continue            # taninmadi veya beyaz listede yok
            urun = f"konsol:{model}"
            try:
                con.execute(
                    "INSERT INTO fiyat_gecmisi (urun, tier, fiyat, "
                    "platform, url, gorsel, tarih) VALUES "
                    "(?, 3, ?, 'sahibinden', ?, ?, "
                    "datetime('now','localtime'))",
                    (urun, f, k["url"], k["gorsel"]))
                _istatistik_guncelle(km, con, urun, 3)
                yazilan += 1
            except Exception:
                _sayac["atlanan"] += 1
        con.commit()
    finally:
        try:
            con.close()
        except Exception:
            pass

    for k in kartlar:
        gorulen.add("k" + k["id"])
    _gorulen_kaydet()
    _sayac["konsol_yazilan"] = _sayac.get("konsol_yazilan", 0) + yazilan
    return yazilan, len(yeni), len(kartlar)


def durum():
    """kelepir_hafiza.db'de kac kayit var?"""
    try:
        con = sqlite3.connect(str(APEX / "kelepir_hafiza.db"), timeout=10)
        n = con.execute("SELECT COUNT(*) FROM fiyat_gecmisi").fetchone()[0]
        bugun = con.execute(
            "SELECT COUNT(*) FROM fiyat_gecmisi WHERE tarih > date('now')"
        ).fetchone()[0]
        con.close()
        return {**_sayac, "db_toplam": n, "db_bugun": bugun,
                "tg_acik": TG_ACIK, "tg_kuyruk": _tg_kuyruk.qsize()}
    except Exception as e:
        return {**_sayac, "hata": str(e)[:80]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("apex yolu:", APEX, "| var mi:", APEX.exists())
    print("scraper kanali:", "ACIK" if TG_ACIK else "KAPALI",
          f"(chat={APEX_CHAT})" if APEX_CHAT else "")
    m = motor()
    print("motor:", "TAMAM" if m else "YOK")
    print("durum:", json.dumps(durum(), ensure_ascii=False))
