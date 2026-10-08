"""
OTO AVCISI — kelepir skorlamasini Telegram bildirimine baglar.

NE ISE YARAR
    Kanala bugune kadar "yeni ilan" duserdi (dakikada ~3, okunamaz).
    Bu modul "DEGERLI ilan" dusuruyor: kelepir.py'nin katmanli
    regresyon modeli piyasanin altinda fiyatlanmis ilanlari buluyor,
    biz de sadece onlari bildiriyoruz.

OLCUM (08.10.2026 21:14, 28.608 ilanlik havuz)
    skorlama suresi      : 0.6 sn  (yani sik calistirmak bedava)
    skorlanan            : 18.764
    aday                 : 628  (VURGUN 17 / FIRSAT 151 / IZLE 460)
    bunlardan sahibinden : 252  (VURGUN 6 / FIRSAT 62 / IZLE 184)
    -> VURGUN+FIRSAT & sahibinden = 68 tarihsel aday. Ileriye donuk
       sadece YENI olanlar bildirilecegi icin saatte birkac tane.

TASARIM KARARLARI
  1) CATCH-UP KORUMASI: ilk kosuda mevcut adaylarin HEPSI "gorulmus"
     isaretlenir ve HICBIRI bildirilmez. Yoksa ilk acilista 68 mesaj
     birden duser. (Apex'teki kelepir_avci de ayni korumayi kullaniyor.)
  2) SADECE SAHIBINDEN (varsayilan): kullanici karari "galeri tarafi
     gereksiz copluk". Ustelik galeri fiyatlari bayi kari tasidigi icin
     referansi yukari cekiyor ve sıradan ilanlari kelepir gibi
     gosteriyordu; sahibinden-sahibinden kiyasi daha dogru.
  3) SEVIYE ESIGI (varsayilan VURGUN+FIRSAT): IZLE seviyesinde guven
     dusuk (R2 0.21-0.30, emsal az) — istatistik gurultusu kanala
     dusmesin. ayar.json'dan genisletilebilir.
  4) IZ KAYDI oto_hafiza.db icinde 'avci_goruldu' tablosunda; yeniden
     baslatmada tekrar bildirim YOK.
  5) Bildirim oto_tg uzerinden gider (ayni kuyruk). Hacim dusuk oldugu
     icin ham akisla cakismiyor.

DURUSTLUK NOTU — mesajda ne yazmiyoruz: bu model fiyat/km regresyonu;
aracin hasar kaydini, bakim gecmisini, fotograftaki durumu BILMIYOR.
Derin indirim bazen firsat degil SORUN isaretidir (parca satisi, hatali
fiyat, dolandiricilik). Mesajda guven ve emsal sayisi gosterilir ki
kullanici kendi karari icin agirlik verebilsin.

KULLANIM
    py -3.12 oto_avci.py            # dongu (varsayilan 180 sn)
    py -3.12 oto_avci.py --kuru     # bildirim YOK, ne giderdi yazar
    py -3.12 oto_avci.py --sifirla  # mevcut adaylari gorulmus isaretle
    py -3.12 oto_avci.py --tek      # tek tur calis ve cik
"""
import json
import os
import queue
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
HAFIZA = ROOT / "oto_hafiza.db"
AYAR = ROOT / "ayar.json"

# ── KENDI KANALI (08.10.2026) ─────────────────────────────────────────
# Kullanici karari: kelepir bildirimleri AYRI kanala dusecek. Sebep
# ikili: (1) ham akis dakikada ~3 mesaj, kelepir onun arasinda kaybolur,
# (2) ham akisin kuyrugu dolu oldugunda kelepir mesaji arkada beklerdi —
# oysa kelepir ZAMANA DUYARLI (iyi ilan dakikalar icinde gidiyor).
# Bu yuzden avcinin KENDI kuyrugu ve KENDI hedefi var.
#
# .env:
#   OTO_AVCI_TOKEN    (bos ise TELEGRAM_TOKEN kullanilir)
#   OTO_AVCI_CHAT_ID  (bos ise TELEGRAM_CHAT_ID kullanilir; virgulle
#                      ayrilmis liste olabilir)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

_TOKEN = ((os.getenv("OTO_AVCI_TOKEN") or "").strip()
          or (os.getenv("TELEGRAM_TOKEN") or "").strip())
_CHAT = ((os.getenv("OTO_AVCI_CHAT_ID") or "").strip()
         or (os.getenv("TELEGRAM_CHAT_ID") or "").strip())
HEDEFLER = [x.strip() for x in _CHAT.split(",") if x.strip()]
TG_ACIK = bool(_TOKEN and HEDEFLER)
AYRI_KANAL = bool((os.getenv("OTO_AVCI_CHAT_ID") or "").strip())

_kuyruk = queue.Queue(maxsize=300)
_isci_basladi = False
_kilit = threading.Lock()
_tg_sayac = {"gonderildi": 0, "hata": 0}


def _tg_istek(uc, alan):
    veri = urllib.parse.urlencode(alan).encode("utf-8")
    istek = urllib.request.Request(
        f"https://api.telegram.org/bot{_TOKEN}/{uc}", data=veri)
    with urllib.request.urlopen(istek, timeout=20) as c:
        return json.loads(c.read().decode("utf-8"))


def _tek_hedefe(hedef, metin):
    try:
        c = _tg_istek("sendMessage", {
            "chat_id": hedef, "text": metin, "parse_mode": "HTML",
            "disable_web_page_preview": "true"})
        return bool(c.get("ok"))
    except Exception as e:
        print(f"[avci tg] {hedef}: {str(e)[:90]}")
        return False


def _tg_isci():
    # Aliciları PARALEL yolla: bu baglantidan Telegram'a her istek ~7 sn
    # (08.10 olcumu); sirayla yollamak bekleyen kelepiri geciktirir.
    while True:
        metin = _kuyruk.get()
        try:
            if len(HEDEFLER) > 1:
                with ThreadPoolExecutor(
                        max_workers=min(4, len(HEDEFLER))) as ic:
                    ok = any(ic.map(
                        lambda h: _tek_hedefe(h, metin), HEDEFLER))
            else:
                ok = _tek_hedefe(HEDEFLER[0], metin)
            _tg_sayac["gonderildi" if ok else "hata"] += 1
        except Exception as e:
            _tg_sayac["hata"] += 1
            print(f"[avci tg] isci: {str(e)[:90]}")
        finally:
            _kuyruk.task_done()
        time.sleep(1.0)


def tg_yolla(metin):
    global _isci_basladi
    if not TG_ACIK:
        return
    with _kilit:
        if not _isci_basladi:
            threading.Thread(target=_tg_isci, daemon=True,
                             name="avci_tg").start()
            _isci_basladi = True
    try:
        _kuyruk.put_nowait(metin)
    except queue.Full:
        _tg_sayac["hata"] += 1

VARSAYILAN_ARALIK = 180          # sn
VARSAYILAN_SEVIYE = ("VURGUN", "FIRSAT")


def ayar_oku():
    try:
        d = json.loads(AYAR.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {
        "aralik": int(d.get("avci_aralik_sn") or VARSAYILAN_ARALIK),
        "seviyeler": tuple(d.get("avci_seviyeler")
                           or VARSAYILAN_SEVIYE),
        "sadece_sahibinden": d.get("avci_sadece_sahibinden") is not False,
        "tur_tavan": int(d.get("avci_tur_tavan") or 5),
    }


def db():
    con = sqlite3.connect(HAFIZA, timeout=15.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    return con


def tablo_hazirla(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS avci_goruldu (
            ilan_id  TEXT PRIMARY KEY,
            seviye   TEXT,
            sapma    REAL,
            tarih    TEXT
        )
    """)
    # ── KANIT TABLOSU (08.10.2026 21:30) ─────────────────────────────
    # NEDEN: "calistikca ustune koyarak optimale cevirmek" icin once
    # KANIT lazim. Bugun iki iyilestirme fikrini olctum ve IKISI DE
    # cikti: (a) km ekstrapolasyonu -> 66 adayin 0'i emsal araliginin
    # disinda, (b) cop ilanlar -> basliginda risk kelimesi olan 0/66.
    # Yani model tweak'i icin elimizde dayanak YOK; hangi esigin ise
    # yaradigini bilmiyoruz. Bu tablo her bildirimi OZELLIKLERIYLE
    # saklar; birkac hafta sonra "hangi seviye/guven/emsal araligi
    # gercekten iyi sonuc verdi" sorusu VERIYLE cevaplanabilir.
    # Sonuc kolonlari (sonuc, sonuc_tarih, son_fiyat_gorulen) sonradan
    # doldurulacak: ilan listeden kaybolduysa muhtemelen SATILDI,
    # fiyati dustuyse pahaliymis.
    con.execute("""
        CREATE TABLE IF NOT EXISTS avci_bildirim (
            ilan_id    TEXT PRIMARY KEY,
            tarih      TEXT,
            seviye     TEXT,
            sapma      REAL,
            akran_sapma REAL,
            beklenen   REAL,
            fiyat      REAL,
            km         INTEGER,
            yil        INTEGER,
            marka      TEXT,
            seri       TEXT,
            model      TEXT,
            il         TEXT,
            kimden     TEXT,
            katman     TEXT,
            emsal_n    INTEGER,
            r2         REAL,
            guven      REAL,
            url        TEXT,
            sonuc      TEXT,
            sonuc_tarih TEXT
        )
    """)
    con.commit()


def bildirim_yaz(con, s, I):
    """Bildirilen ilani TUM ozellikleriyle kaydet (sonraki analiz icin)."""
    r = s["r"]
    try:
        con.execute("""
            INSERT OR REPLACE INTO avci_bildirim
              (ilan_id, tarih, seviye, sapma, akran_sapma, beklenen,
               fiyat, km, yil, marka, seri, model, il, kimden, katman,
               emsal_n, r2, guven, url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r[I["ilan_id"]],
            datetime.now().isoformat(timespec="seconds"),
            s["seviye"], s["sapma"], s.get("akran_sapma"),
            s["beklenen"], r[I["fiyat"]], r[I["km"]], r[I["yil"]],
            r[I["marka"]], r[I["seri"]], r[I["model"]], r[I["il"]],
            r[I["kimden"]], s["katman"], s["n"], s["r2"], s["guven"],
            r[I["url"]],
        ))
    except Exception as e:
        print(f"[avci] kanit yazilamadi: {str(e)[:90]}")


def goruldu_mu(con, ilan_id):
    return con.execute("SELECT 1 FROM avci_goruldu WHERE ilan_id=?",
                       (ilan_id,)).fetchone() is not None


def goruldu_yaz(con, ilan_id, seviye, sapma):
    con.execute(
        "INSERT OR REPLACE INTO avci_goruldu "
        "(ilan_id, seviye, sapma, tarih) VALUES (?,?,?,?)",
        (ilan_id, seviye, sapma,
         datetime.now().isoformat(timespec="seconds")))


def _tl(x):
    try:
        return f"{float(x):,.0f}".replace(",", ".")
    except Exception:
        return "?"


def _km(x):
    try:
        return f"{int(x):,}".replace(",", ".")
    except Exception:
        return "?"


def _kacar(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def mesaj_kur(s, I):
    r = s["r"]
    arac = " ".join(str(r[I[k]] or "") for k in ("marka", "seri")).strip()
    model = str(r[I["model"]] or "")[:38]
    yer = "/".join(x for x in (str(r[I["il"]] or ""),
                               str(r[I["ilce"]] or "")) if x)
    isaret = "🔥" if s["seviye"] == "VURGUN" else "🎯"
    akran = (f" · akran %{s['akran_sapma']:.0f}"
             if s.get("akran_sapma") is not None else "")
    return (
        f"{isaret} <b>{s['seviye']}</b>  %{s['sapma']:.0f}{akran}\n"
        f"{_kacar(arac)} {_kacar(model)} · {r[I['yil']]}\n"
        f"<b>{_tl(r[I['fiyat']])} TL</b>  "
        f"(beklenen {_tl(s['beklenen'])})\n"
        f"{_km(r[I['km']])} km · {_kacar(yer)} · {r[I['kimden']]}\n"
        f"<i>guven {s['guven']:.0f} · emsal {s['n']} · "
        f"{s['katman']} R2 {s['r2']:.2f}</i>\n"
        f"<a href=\"{_kacar(r[I['url']])}\">ilana git</a>"
    )


def bir_tur(kuru=False, sifirla=False):
    """Doner: (bildirilen, atlanan_goruldu, aday_toplam)."""
    import kelepir
    I = kelepir.I
    a = ayar_oku()

    try:
        rows, modeller, skorlar, adaylar = kelepir.calistir(db=str(HAFIZA))
    except Exception as e:
        print(f"[avci] skorlama hatasi: {str(e)[:140]}")
        return 0, 0, 0

    secilen = [s for s in adaylar if s.get("seviye") in a["seviyeler"]]
    if a["sadece_sahibinden"]:
        secilen = [s for s in secilen
                   if str(s["r"][I["kimden"]]) == "sahibinden"]
    # Sapmasi en derin olan once
    secilen.sort(key=lambda s: s["sapma"])

    con = db()
    tablo_hazirla(con)
    try:
        ilk_kosu = con.execute(
            "SELECT COUNT(*) FROM avci_goruldu").fetchone()[0] == 0

        if sifirla or (ilk_kosu and not kuru):
            n = 0
            for s in secilen:
                goruldu_yaz(con, s["r"][I["ilan_id"]], s["seviye"],
                            s["sapma"])
                n += 1
            con.commit()
            sebep = ("--sifirla" if sifirla else
                     "ILK KOSU (catch-up korumasi)")
            print(f"[avci] {sebep}: {n} aday 'gorulmus' isaretlendi, "
                  f"bildirim YAPILMADI")
            return 0, n, len(secilen)

        yeni = [s for s in secilen
                if not goruldu_mu(con, s["r"][I["ilan_id"]])]
        atlanan = len(secilen) - len(yeni)

        if not yeni:
            return 0, atlanan, len(secilen)

        if kuru:
            print(f"[avci] KURU MOD — {len(yeni)} ilan bildirilecekti:")
            for s in yeni[:a["tur_tavan"]]:
                r = s["r"]
                print(f"   {s['seviye']:<7} %{s['sapma']:>5.0f}  "
                      f"{_tl(r[I['fiyat']]):>11} TL  "
                      f"{_km(r[I['km']]):>9} km  {r[I['yil']]}  "
                      f"{r[I['marka']]} {r[I['seri']]} "
                      f"{str(r[I['model']])[:24]}  "
                      f"[guven {s['guven']:.0f} emsal {s['n']}]")
            if len(yeni) > a["tur_tavan"]:
                print(f"   ... +{len(yeni) - a['tur_tavan']} tane daha")
            return 0, atlanan, len(secilen)

        if not TG_ACIK:
            print("[avci] Telegram kapali (.env'de token/chat yok)")
            return 0, atlanan, len(secilen)

        gonderilen = 0
        for s in yeni[:a["tur_tavan"]]:
            r = s["r"]
            try:
                tg_yolla(mesaj_kur(s, I))
                goruldu_yaz(con, r[I["ilan_id"]], s["seviye"], s["sapma"])
                bildirim_yaz(con, s, I)
                gonderilen += 1
                print(f"[avci] BILDIRILDI {s['seviye']} %{s['sapma']:.0f} "
                      f"{r[I['marka']]} {r[I['seri']]} "
                      f"{_tl(r[I['fiyat']])} TL")
            except Exception as e:
                print(f"[avci] bildirim hatasi: {str(e)[:100]}")
        # Tavani asanlar bu turda gorulmus SAYILMAZ; sonraki turda gider.
        con.commit()
        return gonderilen, atlanan, len(secilen)
    finally:
        con.close()


def main():
    kuru = "--kuru" in sys.argv
    sifirla = "--sifirla" in sys.argv
    tek = "--tek" in sys.argv or kuru or sifirla

    print("=" * 62)
    print("OTO AVCISI — kelepir bildirimi")
    print(f"  DB      : {HAFIZA.name}")
    a = ayar_oku()
    print(f"  seviye  : {', '.join(a['seviyeler'])}")
    print(f"  filtre  : {'sadece sahibinden' if a['sadece_sahibinden'] else 'hepsi'}")
    print(f"  aralik  : {a['aralik']} sn | tur tavani: {a['tur_tavan']}")
    print(f"  kanal   : {'AYRI kanal' if AYRI_KANAL else 'ham akisla AYNI kanal'}"
          f" | hedef sayisi: {len(HEDEFLER)}"
          f" | {'ACIK' if TG_ACIK else 'KAPALI'}")
    if kuru:
        print("  MOD     : KURU (bildirim yok)")
    print("=" * 62)

    while True:
        t0 = time.time()
        g, atl, top = bir_tur(kuru=kuru, sifirla=sifirla)
        if g or kuru or sifirla:
            print(f"{datetime.now():%H:%M:%S}  bildirilen={g} "
                  f"gorulmus_atlanan={atl} aday={top} "
                  f"({time.time() - t0:.1f} sn)")
        if tek:
            break
        time.sleep(ayar_oku()["aralik"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except KeyboardInterrupt:
        print("\nkapatildi.")
