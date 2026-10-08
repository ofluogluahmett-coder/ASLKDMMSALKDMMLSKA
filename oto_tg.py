"""
TELEGRAM BESLEMESI — "yeni ilan girdi" akisi (@otobotpro_bot).

NE DEGIL: bu kelepir avcisi DEGIL. Skorlama, puan, FIRSAT/VURGUN yok.
PC botunun scraper kanali gibi duz bir akis: uzanti yeni bir ilan
yakaladiginda ilan dusuyor. Kelepir bildirimi ayri bir is (kelepir.py).

TASARIM KARARLARI (hepsi bir sebebe dayaniyor)

  1) KUYRUK + TEK ISCI. Ana akis (HTTP isleyici) ASLA Telegram'i
     beklemez; kuyruga atar (~0 ms) ve devam eder. PC botunda senkron
     gonderim tur suresini 4-5 dakikaya cikarmisti; ayni hataya
     dusmuyoruz.

  2) GORSEL INDIRILMIYOR. Kullanici "ilan fotolari alinmasin" dedi.
     sendPhoto'ya URL veriyoruz, fotoyu Telegram kendi sunucusundan
     cekiyor — bizim IP'mizden shbdn.com'a tek byte gitmiyor. Foto
     reddedilirse metin mesajina duser.

  3) ILK TUR SESSIZ. Sunucu her acildiginda ilk tur 50 ilan "ilk kez
     gorulmus" olur (gece birikenler). Onlari bildirmek 50 mesajlik
     spam olur. Bu yuzden ilk tur SESSIZCE ozumsenir — PC botundaki
     "catch-up spam" korumasinin aynisi.

  4) TUR TAVANI. Bir turda en fazla OTO_TG_TUR_TAVAN ilan bildirilir,
     fazlasi tek satirda ozetlenir. Yogun saatte 30 ilan birden girerse
     kanal okunamaz hale gelmesin.

  5) ID SU SEVIYESI (watermark). sahibinden'de eski ilanlar "doping"
     ile tarihi tazelenip basa donuyor; gorsel bir isaret YOK. Ama ilan
     ID'leri global ve artan: ID'si o ana kadar gorulen en buyuk ID'den
     BUYUKSE ilan gercekten yeni, kucukse eski bir ilan yukari itilmis.
     Mesajda bu ayrim gosterilir (YENI / tazelenmis).

Ayar: .env -> TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, OTO_TG_BESLEME,
      OTO_TG_ARA, OTO_TG_TUR_TAVAN
"""
import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request

TOKEN = (os.getenv("TELEGRAM_TOKEN") or "").strip()
CHAT = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
ACIK = (os.getenv("OTO_TG_BESLEME", "1") != "0") and bool(TOKEN and CHAT)
ARA = float(os.getenv("OTO_TG_ARA", "3.5"))
TUR_TAVAN = int(os.getenv("OTO_TG_TUR_TAVAN", "12"))

# ── DOPING FILTRESI (08.10.2026 — OLCUMLE BULUNDU) ───────────────────
# Bir turda DB'ye 50 ilan "ilk kez gorulmus" olarak girdi; incelendiginde
# sadece 9'u gercekten yeniydi. Kalani doping ile yukari itilmis ESKI
# ilanlardi (ID'leri su seviyesinin 1 MILYONDAN fazla altinda). Bunlari
# "yeni ilan" diye bildirmek beslemeyi coplestirir.
#
# ID uzaklik dagilimi (10:34 turu, 50 kayit):
#     su seviyesinin USTUNDE ................  9
#     0-20 bin ALTI .........................  23   <- kesintisiz kume
#     20-100 bin ALTI .......................   0   <- BOS: dogal sinir
#     100 bin - 1 milyon ALTI ...............   1
#     1 milyon+ ALTI ........................  17   <- doping
# En yakin kayitlarin uzakligi: +1174 +980 ... +54 -9 -214 -365 -491
# yani su seviyesinin hemen altindaki kume SUREKLI; orada kesmek yanlis
# olurdu (biz o ilanlari henuz kaydetmemisiz, ilan yine de taze).
# 20-100 bin arasinin BOS olmasi bant sinirinin guvenli oldugunu gosterir.
#
# SINIR: ilan onayi gecikirse (ID uretilir ama liste'ye sonra duser) bant
# disinda kalabilir ve "yeni" sayilmaz. Bant bu yuzden var; tek basina
# "ID > su seviyesi" kurali o ilanlari KACIRIYORDU.
YENI_BANT = int(os.getenv("OTO_TG_YENI_BANT", "20000"))
SADECE_YENI = os.getenv("OTO_TG_SADECE_YENI", "1") != "0"

# ── ILK TUR KORUMASI KALDIRILDI (08.10.2026) ───────────────────────────
# Tarih: once "ilk tur HER ZAMAN sessiz" kuraliydi; sonra esige baglandi
# (>15 ilan). IKISI DE YANLISTI. Olcum: sunucu gun icinde her yeniden
# baslatildiginda o turun ilanlari yutuldu — bir keresinde 50 GERCEK yeni
# ilan sessizce gitti ve kullanici hakli olarak "5 dakikadir ilan
# dusmuyor" dedi. Sorun sahibinden'de degil, bu susturucudaydi.
# Ikinci bir susturucuya GEREK YOK, cunku sel zaten iki katmanla
# engelleniyor: (1) bant filtresi sadece TAZE ilanlari geciriyor,
# (2) TUR_TAVAN bir turda en fazla 12 mesaj yolluyor, kalani tek satirda
# ozetliyor. En kotu durumda (sunucu saatlerce kapali kalip sabah
# acilirsa) 12 mesaj + bir ozet satiri gider — ki o ilanlar GERCEKTEN
# yenidir ve bildirilmeleri DOGRUDUR.
# Cok buyuk yigin icin yine de bir emniyet supabi birakiliyor; 0 =
# kapali, pratikte devre disi.
ILK_TUR_ESIK = int(os.getenv("OTO_TG_ILK_TUR_ESIK", "0"))

API = "https://api.telegram.org/bot" + TOKEN + "/"

_kuyruk = queue.Queue(maxsize=500)
_isci_basladi = False
_kilit = threading.Lock()
_sayac = {"gonderildi": 0, "hata": 0, "dusurulen": 0, "atlanan_ilk_tur": 0,
          "atlanan_doping": 0}


def _istek(uc, alan):
    veri = urllib.parse.urlencode(alan).encode("utf-8")
    istek = urllib.request.Request(API + uc, data=veri)
    with urllib.request.urlopen(istek, timeout=20) as c:
        return json.loads(c.read().decode("utf-8"))


def _gonder_tek(mesaj):
    """Doner: True/False. Foto reddedilirse metne duser."""
    metin, foto = mesaj["metin"], mesaj.get("foto")
    if foto:
        try:
            c = _istek("sendPhoto", {
                "chat_id": CHAT, "photo": foto, "caption": metin,
                "parse_mode": "HTML"})
            if c.get("ok"):
                return True
        except Exception:
            pass        # foto tutmadi -> metin denenir
    try:
        c = _istek("sendMessage", {
            "chat_id": CHAT, "text": metin, "parse_mode": "HTML",
            "disable_web_page_preview": "true"})
        return bool(c.get("ok"))
    except Exception as e:
        print(f"  [tg] gonderilemedi: {str(e)[:100]}")
        return False


def _kanal_kaydi(mesaj, ok):
    """Kanala NE gittiginin kalici kaydi. Boylece "kanalda ne var"
    sorusu log'dan cevaplanabilir (kendi gonderdigimiz mesajlari
    Telegram API'sinden geri okuyamiyoruz)."""
    try:
        from pathlib import Path
        from datetime import datetime as _dt
        yol = Path(__file__).parent / "tg_gunlugu.csv"
        yeni = not yol.exists()
        tek = " | ".join(x.strip() for x in
                         _etiket_temizle(mesaj["metin"]).splitlines() if x.strip())
        with yol.open("a", encoding="utf-8") as f:
            if yeni:
                f.write("zaman,durum,mesaj\n")
            f.write(f"{_dt.now():%Y-%m-%d %H:%M:%S},"
                    f"{'gitti' if ok else 'HATA'},\"{tek[:300]}\"\n")
    except Exception:
        pass


def _etiket_temizle(s):
    import re as _re
    return _re.sub(r"<[^>]+>", "", s or "").replace('"', "'")


def _isci():
    while True:
        mesaj = _kuyruk.get()
        try:
            ok = _gonder_tek(mesaj)
            _kanal_kaydi(mesaj, ok)
            if ok:
                _sayac["gonderildi"] += 1
            else:
                _sayac["hata"] += 1
        except Exception as e:
            _sayac["hata"] += 1
            print(f"  [tg] isci hatasi: {str(e)[:100]}")
        finally:
            _kuyruk.task_done()
        time.sleep(ARA)     # Telegram kanal limiti (~20 msg/dk)


def _isciyi_baslat():
    global _isci_basladi
    with _kilit:
        if _isci_basladi or not ACIK:
            return
        threading.Thread(target=_isci, daemon=True, name="tg").start()
        _isci_basladi = True


def _kuyruga(metin, foto=None):
    _isciyi_baslat()
    try:
        _kuyruk.put_nowait({"metin": metin, "foto": foto})
    except queue.Full:
        # Kuyruk dolduysa EN ESKIYI dusur; yeni ilan daha degerli.
        try:
            _kuyruk.get_nowait()
            _sayac["dusurulen"] += 1
            _kuyruk.put_nowait({"metin": metin, "foto": foto})
        except Exception:
            _sayac["dusurulen"] += 1


def _tl(x):
    try:
        return f"{float(x):,.0f}".replace(",", ".") + " TL"
    except Exception:
        return "? TL"


def _km(x):
    try:
        return f"{int(x):,}".replace(",", ".") + " km"
    except Exception:
        return "? km"


def _kacar(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def ilan_metni(ilan, taze=True):
    """ilan: oto_bot.parse_ilanlar'in urettigi sozluk."""
    arac = " ".join(x for x in (str(ilan.get("marka") or ""),
                                str(ilan.get("seri") or "")) if x)
    model = str(ilan.get("model") or "")
    yil = ilan.get("yil") or "?"
    yer = "/".join(x for x in (str(ilan.get("il") or ""),
                               str(ilan.get("ilce") or "")) if x)
    kimden = ("galeriden" if ilan.get("kimden") == "galeriden"
              else "sahibinden")
    bas = "🚗 <b>YENİ</b>" if taze else "🔁 tazelenmiş"
    satirlar = [
        f"{bas} · {_kacar(arac)} {_kacar(model)}".rstrip(),
        f"<b>{_tl(ilan.get('fiyat'))}</b> · {yil} · "
        f"{_km(ilan.get('km'))}",
        f"{_kacar(yer)} · {kimden}",
    ]
    bsk = str(ilan.get("baslik") or "").strip()
    if bsk:
        satirlar.append(f"<i>{_kacar(bsk[:90])}</i>")
    url = ilan.get("url") or ""
    if url:
        satirlar.append(f'<a href="{_kacar(url)}">ilana git</a>')
    return "\n".join(satirlar)


def ilanlari_bildir(ilanlar, su_seviyesi=None, ilk_tur=False):
    """Yeni ilanlari kuyruga atar.

    Doner: (gonderilen, ozetlenen, atlanan_doping)

    ilanlar     : oto_bot.parse_ilanlar sozlukleri (SADECE DB'ye ilk kez
                  girenler)
    su_seviyesi : o ana kadar gorulen en buyuk ilan ID'si
    ilk_tur     : True ise HICBIR SEY gonderilmez (catch-up spam korumasi)
    """
    if not ACIK or not ilanlar:
        return 0, 0, 0
    # Emniyet supabi; ILK_TUR_ESIK=0 ise devre disi (varsayilan).
    if ilk_tur and ILK_TUR_ESIK and len(ilanlar) > ILK_TUR_ESIK:
        _sayac["atlanan_ilk_tur"] += len(ilanlar)
        return 0, len(ilanlar), 0

    def _id(i):
        try:
            return int(i.get("ilan_id") or 0)
        except Exception:
            return 0

    # ID'si buyuk olan daha yeni -> once o gider.
    sirali = sorted(ilanlar, key=_id, reverse=True)

    # Doping ayrimi: bant icindekiler taze, altindakiler yukari itilmis
    # eski ilanlar. Su seviyesi yoksa (ilk kosu) ayrim yapilamaz.
    if su_seviyesi:
        esik = int(su_seviyesi) - YENI_BANT
        taze = [i for i in sirali if _id(i) > esik]
        doping = [i for i in sirali if _id(i) <= esik]
    else:
        taze, doping = sirali, []

    hedef = taze if SADECE_YENI else sirali
    if SADECE_YENI and doping:
        _sayac["atlanan_doping"] += len(doping)

    atilan = 0
    for ilan in hedef[:TUR_TAVAN]:
        _kuyruga(ilan_metni(ilan, _id(ilan) > int(su_seviyesi or 0)),
                 ilan.get("gorsel") or None)
        atilan += 1
    kalan = len(hedef) - atilan
    if kalan > 0:
        _kuyruga(f"… bu turda <b>{kalan}</b> yeni ilan daha girdi "
                 f"(kanal dolmasin diye ozetlendi).")
    return atilan, max(0, kalan), len(doping) if SADECE_YENI else 0


def haber(metin):
    """Sistem mesaji (baslangic, bekci uyarisi vb.)."""
    if ACIK:
        _kuyruga(metin)


def durum():
    return {**_sayac, "acik": ACIK, "kuyruk": _kuyruk.qsize()}


if __name__ == "__main__":
    # Dogrudan calistirilirsa: baglanti + tek test mesaji
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env")
    # env yeniden okundu; modul sabitleri import aninda alinmisti
    TOKEN = (os.getenv("TELEGRAM_TOKEN") or "").strip()
    CHAT = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    API = "https://api.telegram.org/bot" + TOKEN + "/"
    ACIK = bool(TOKEN and CHAT)
    _isci_basladi = True        # isciyi baslatma, senkron gonder
    print("token:", (TOKEN[:12] + "...") if TOKEN else "(bos)",
          "| chat:", CHAT or "(bos)")
    try:
        me = json.load(urllib.request.urlopen(API + "getMe", timeout=15))
        print("bot:", me["result"]["username"])
    except Exception as e:
        print("getMe hatasi:", str(e)[:120])
    ok = _gonder_tek({"metin": "🚗 <b>Oto Kelepir</b> — besleme baglandi.\n"
                               "Bu kanal <i>yeni ilan akisi</i>; kelepir "
                               "avcisi degil."})
    print("test mesaji:", "GITTI" if ok else "GITMEDI")
