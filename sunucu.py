"""
YEREL SUNUCU — uzantinin yolladigi ilanlari DB'ye yazar.

MIMARI (07.10.2026):
    Senin Brave'in (kendi profilin, elle acilmis)
      └─ uzanti/content.js  → sayfanin KENDI XMLHttpRequest yolu
           └─ uzanti/background.js → POST http://127.0.0.1:8765/ilan
                └─ BU DOSYA → oto_tarama.db + oto_hafiza.db (kelepir semasi)

NEDEN: olculen tum alternatifler elendi (IP, cerez, parmak izi, tempo,
parametre, sayfa boyu). Geriye tek fark kaldi: tarayicinin DISARIDAN
surulmesi. Bu mimaride WebDriver yok, CDP yok, otomasyon bayragi yok,
gecici profil yok — cunku tarayici GERCEKTEN normal bir tarayici.
Bu sunucu sahibinden'e HICBIR istek atmaz; sadece uzantidan veri alir.

KULLANIM
    py -3.12 sunucu.py
    (sonra Brave'de uzantiyi yukle: brave://extensions → Gelistirici modu
     → "Paketlenmemis yukle" → oto_kelepir/uzanti klasoru)
    Ardindan sahibinden otomobil listesini bir sekmede ACIK BIRAK.

Durum: http://127.0.0.1:8765/durum
"""
import json
import re
import sqlite3
import sys
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:                                    # .env -> TELEGRAM_TOKEN vb.
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

ROOT = Path(__file__).parent
DB_FILE = ROOT / "oto_tarama.db"
HAM_DOSYA = ROOT / "_canli_sayfa.html"      # teshis icin son liste HTML'i
# Gun boyu kosunun kalici kaydi. stdout log'u yeniden baslatmada
# siliniyor; bu dosya EKLEME modunda, gunun olcumu kaybolmasin.
TUR_CSV = ROOT / "tur_gunlugu.csv"
PORT = 8765

_sayac = {"istek": 0, "ilan": 0, "yeni": 0, "guncel": 0,
          "zengin_yeni": 0, "zengin_guncel": 0,
          "baslangic": datetime.now().isoformat(timespec="seconds"),
          # 08.10.2026: acilista "az once gorulduk" varsayilir. Sebep:
          # sunucu yeniden baslatildiginda son_gorulme bosaliyordu,
          # bekci sunucudan cevap alamayip kendi bayat kaydina dusuyor
          # ve SESSIZLIK sanip sekmeyi bosuna yeniliyordu (olcum:
          # "sekme yenilendi (282 sn sessizdi)" — oysa yeni baslamistik).
          # Acilista elimizde sessizlik KANITI yok; gercek sessizlik
          # zaten esik kadar sonra yine yakalanir.
          "son_gorulme": datetime.now().isoformat(timespec="seconds")}

# Panel icin tur gecmisi (bellekte, son 25 tur)
_gecmis = deque(maxlen=25)

# Gun boyu kosu icin uzun gecmis: (zaman, yeni, zengin_yeni, tg, yol).
# Buradan "son 1 saatte kac tur, kac yeni ilan, EN UZUN BOSLUK kac dk"
# hesaplanir — kullanici is yerinden tek bakista gorsun diye.
_uzun_gecmis = deque(maxlen=3000)

# Icerik bayatligi olcumu: son turun parmak izi ve kac turdur AYNI
# kaldigi. Ayrinti: sayfa_parmak_izi()
_son_parmak = ""
_ayni_sayac = 0

# Uzanti bekcisinin dakikalik nabzi (sekme duruyor mu, veri geliyor mu).
# Bu sayede "tarayici kapanmis" ile "sekme donmus" ayirt edilir.
_bekci = {}

# ── KOPRU (zengin sema) ───────────────────────────────────────────────
# Uzanti ham liste HTML'ini de yolluyor; onu otobotun KANITLANMIS
# ayristiricisindan gecirip oto_hafiza.db'ye yaziyoruz. kelepir.py
# skorlamasi oradan besleniyor. ThreadingHTTPServer oldugu icin hem
# ice aktarma hem yazma tek kilit altinda (SQLite + modul yuklemesi).
_kopru = None
_kopru_kilit = threading.Lock()

# ── TELEGRAM BESLEMESI ────────────────────────────────────────────────
# "Yeni ilan girdi" akisi (@otobotpro_bot). Kelepir avcisi DEGIL.
try:
    import oto_tg
except Exception as _e:
    oto_tg = None
    print(f"  [tg] modul yuklenemedi: {str(_e)[:100]}")

# ── PC BILESENI KOPRUSU (08.10.2026) ─────────────────────────────────
# Ayni uzanti artik PC bileseni listesini de izliyor. O kategorinin
# verisi apex_predator'un KANITLANMIS motoruna gider (kelepir_hafiza.db)
# ve kelepir_avci.py zaten o DB'yi izledigi icin FIRSAT/VURGUN
# bildirimleri KENDILIGINDEN calisir. Boylece PC botunun WebDriver'i ve
# onunla gelen CF/PX derdi ortadan kalkar.
_pc_kopru = None


def pc_kopru():
    global _pc_kopru
    if _pc_kopru is not None:
        return _pc_kopru
    try:
        import pc_kopru
        _pc_kopru = pc_kopru
        print("  [pc] PC bileseni koprusu AKTIF (apex KelepirMotor)")
    except Exception as e:
        _pc_kopru = False
        print(f"  [pc] kopru yuklenemedi: {str(e)[:120]}")
    return _pc_kopru

# ID SU SEVIYESI — doping ayrimi. sahibinden'de eski ilanlar tarihi
# tazelenip basa donuyor ve gorsel bir isaret YOK; ama ilan ID'leri
# global ve artan. ID'si su seviyesinden buyuk olan GERCEKTEN yeni.
_su_seviyesi = 0
# Sunucu acildiginda ilk tur 50 ilani "ilk kez gorulmus" sayar (gece
# birikenler). Onlari bildirmek 50 mesajlik spam olur -> ilk tur sessiz.
_ilk_tur_gecti = False


def _su_seviyesi_oku():
    """Zengin semadaki en buyuk ilan ID'si = baslangic su seviyesi."""
    global _su_seviyesi
    k = kopru()
    if not k:
        return
    try:
        con = k._baglan()
        r = con.execute("SELECT MAX(CAST(ilan_id AS INTEGER)) "
                        "FROM ilan").fetchone()
        con.close()
        _su_seviyesi = int(r[0] or 0)
        print(f"  [su seviyesi] {_su_seviyesi} "
              f"(bu ID'den buyuk olanlar GERCEKTEN yeni)")
    except Exception as e:
        print(f"  [su seviyesi] okunamadi: {str(e)[:90]}")


def kopru():
    """oto_kopru'yu tembel ice aktar. Basarisizsa False doner (tarama
    akisi bundan ETKILENMEZ — hafif tablo yine yazilir)."""
    global _kopru
    if _kopru is not None:
        return _kopru
    try:
        import oto_kopru
        _kopru = oto_kopru
        print("  [kopru] oto_hafiza.db zengin yazma AKTIF")
    except Exception as e:
        _kopru = False
        print(f"  [kopru] ice aktarilamadi, zengin yazma KAPALI: "
              f"{str(e)[:120]}")
    return _kopru


def db_baglan():
    con = sqlite3.connect(DB_FILE, timeout=15.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=15000")
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
        # Uzantidan gelen zengin alanlar (varsa) — eski DB'ye additive
        for kolon, tip in (("marka", "TEXT"), ("seri", "TEXT"),
                           ("model", "TEXT"), ("il", "TEXT"),
                           ("ilce", "TEXT"), ("kimden", "TEXT"),
                           ("kaynak", "TEXT")):
            try:
                con.execute(f"ALTER TABLE oto_ilan ADD COLUMN {kolon} {tip}")
            except sqlite3.OperationalError:
                pass       # kolon zaten var
        con.execute("CREATE INDEX IF NOT EXISTS idx_oto_ilk "
                    "ON oto_ilan(ilk_gorme)")


_RE_DATA_ID = re.compile(r'data-id="(\d+)"')


def sayfa_parmak_izi(ham):
    """Turun ICERIK parmak izi: (adet, ilk3, kisa_hash).

    NEDEN (08.10.2026): turlar sagliklı gorunuyordu (sayfada=51, yeni=0)
    ama icerigin DEGISTIGINI hic dogrulamadik. Sayfa yenilendikten hemen
    sonraki turlarda 51 kartin 51'i de "hic gorulmemis" cikti — yani XHR
    ile cektigimiz liste DONMUS olabilir ve biz ayni anlik goruntuyu
    tekrar tekrar okuyor olabiliriz. Ayni parmak izi ust uste tekrar
    ediyorsa bayatlik KANITLANIR; degisiyorsa suphe duser.
    Uzantiya dokunmadan olculebilsin diye sunucu tarafinda yapiliyor.
    """
    idler = _RE_DATA_ID.findall(ham or "")
    if not idler:
        return 0, "", ""
    import hashlib
    h = hashlib.sha1(",".join(idler).encode()).hexdigest()[:10]
    return len(idler), ",".join(idler[:3]), h


def _sayi(s):
    t = re.sub(r"[^\d]", "", s or "")
    return float(t) if t else None


def ilan_yaz(kartlar, kaynak="uzanti"):
    """Doner: (yeni, guncel)"""
    simdi = datetime.now().isoformat(timespec="seconds")
    yeni = guncel = 0
    with db_baglan() as con:
        for k in kartlar:
            try:
                iid = str(k.get("id") or "").strip()
                if not iid.isdigit():
                    continue
                fiyat = _sayi(k.get("fiyat"))
                baslik = (k.get("baslik") or "").strip()
                if not baslik or fiyat is None:
                    continue
                var = con.execute("SELECT 1 FROM oto_ilan WHERE ilan_id=?",
                                  (iid,)).fetchone()
                if var:
                    con.execute(
                        "UPDATE oto_ilan SET fiyat=?, son_gorme=? "
                        "WHERE ilan_id=?", (fiyat, simdi, iid))
                    guncel += 1
                else:
                    con.execute(
                        "INSERT INTO oto_ilan (ilan_id, baslik, fiyat, yil, "
                        "km, url, ilk_gorme, son_gorme, marka, seri, model, "
                        "il, ilce, kimden, kaynak) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (iid, baslik, fiyat, k.get("yil") or "",
                         k.get("km") or "", k.get("url") or "", simdi, simdi,
                         k.get("marka") or "", k.get("seri") or "",
                         k.get("model") or "", k.get("il") or "",
                         k.get("ilce") or "",
                         "galeriden" if k.get("magaza") else "sahibinden",
                         kaynak))
                    yeni += 1
            except Exception as e:
                print(f"  [kayit hatasi] {str(e)[:90]}")
                continue
    return yeni, guncel


_CSS = """
body{background:#14161a;color:#e6e6e6;font:14px/1.5 Segoe UI,Arial,sans-serif;
     margin:0;padding:22px}
h1{font-size:19px;margin:0 0 4px}
.alt{color:#8a909a;font-size:12px;margin-bottom:18px}
.kutular{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px}
.kutu{background:#1d2026;border:1px solid #2b3039;border-radius:9px;
      padding:12px 16px;min-width:108px}
.kutu .b{font-size:11px;color:#8a909a;text-transform:uppercase;
         letter-spacing:.5px}
.kutu .d{font-size:23px;font-weight:600;margin-top:3px}
.nabiz{padding:11px 16px;border-radius:9px;font-weight:600;margin-bottom:20px}
.canli{background:#13331f;border:1px solid #1f6b3a;color:#5ee08a}
.sessiz{background:#3a1d1d;border:1px solid #7a2b2b;color:#ff8a8a}
table{border-collapse:collapse;width:100%;max-width:960px;margin-bottom:26px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #262b33}
th{color:#8a909a;font-size:11px;text-transform:uppercase;font-weight:600}
td{font-variant-numeric:tabular-nums}
.y{color:#5ee08a;font-weight:600}
.s{color:#6a7180}
h2{font-size:13px;color:#8a909a;text-transform:uppercase;letter-spacing:.5px;
   margin:0 0 8px}
a{color:#7fb5ff;text-decoration:none}
"""


def _kutu(baslik, deger):
    return ('<div class="kutu"><div class="b">' + baslik +
            '</div><div class="d">' + str(deger) + '</div></div>')


def _kacar(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _bekci_satiri():
    """Uzanti bekcisinin son raporu — 'tarayici kapanmis' ile 'sekme
    donmus' ayrimini kullaniciya tek satirda gosterir."""
    if not _bekci:
        return ('<div class="alt">Uzanti bekcisi henuz rapor vermedi '
                '(dakikada bir rapor verir; uzantiyi yeni yuklediysen '
                '1 dakika bekle).</div>')
    yas = None
    try:
        yas = int((datetime.now()
                   - datetime.fromisoformat(_bekci["zaman"])).total_seconds())
    except Exception:
        pass
    sekme = _bekci.get("sekme", "?")
    eylem = _bekci.get("eylem", "?")
    sessiz = _bekci.get("sessiz_sn")
    if yas is not None and yas > 180:
        return ('<div class="nabiz sessiz">UZANTI BEKCISI SUSTU — ' +
                str(yas // 60) + ' dakikadir rapor yok. Brave kapali veya '
                'uzanti devre disi olabilir.</div>')
    p = ['<div class="alt">Bekci: liste sekmesi <b>', str(sekme),
         '</b> adet']
    if _bekci.get("donmus"):
        p.append(' (' + str(_bekci["donmus"]) + ' tanesi tarayici '
                 'tarafindan DONDURULMUS)')
    if sessiz is not None:
        p.append(' &middot; son veri ' + str(sessiz) + ' sn once')
    p.append(' &middot; durum: ' + _kacar(eylem))
    if yas is not None:
        p.append(' &middot; rapor ' + str(yas) + ' sn once')
    p.append('</div>')
    return "".join(p)


def _saatlik_ozet():
    """Son 1 saatin ozeti + EN UZUN BOSLUK. Yogun saatte ilan kacirip
    kacirmadigimizi gosteren asil sayi bu boslugtur."""
    if not _uzun_gecmis:
        return '<div class="alt">Son 1 saat: henuz tur yok.</div>'
    simdi = datetime.now()
    son = [k for k in _uzun_gecmis
           if (simdi - k[0]).total_seconds() <= 3600]
    if not son:
        return '<div class="alt">Son 1 saatte tur yok.</div>'
    tur = len(son)
    yeni = sum(k[1] for k in son)
    zengin = sum(k[2] for k in son)
    tg = sum(k[3] for k in son)
    # Bosluklar: turlar arasi + son turdan SIMDIYE kadar gecen sure
    anlar = [k[0] for k in son]
    bosluklar = [(anlar[i + 1] - anlar[i]).total_seconds()
                 for i in range(len(anlar) - 1)]
    bosluklar.append((simdi - anlar[-1]).total_seconds())
    enb = max(bosluklar)
    ort = sum(bosluklar) / len(bosluklar)
    sinif = "y" if enb <= 300 else "s"
    return ('<div class="alt">Son 1 saat: <b>' + str(tur) +
            '</b> tur &middot; <b>' + str(yeni) +
            '</b> yeni kayit &middot; <b>' + str(zengin) +
            '</b> zengin semaya &middot; <b>' + str(tg) +
            '</b> Telegram &middot; turlar arasi ortalama <b>' +
            str(int(ort)) + ' sn</b> &middot; <span class="' + sinif +
            '">en uzun bosluk ' + str(int(enb)) + ' sn</span></div>')


def panel_html():
    """Insan okuyacak canli durum sayfasi — 10 sn'de bir kendini yeniler."""
    # Nabiz: son turdan bu yana kac saniye gecti?
    gecen = None
    sg = _sayac.get("son_gorulme")
    if sg:
        try:
            gecen = int((datetime.now()
                         - datetime.fromisoformat(sg)).total_seconds())
        except Exception:
            gecen = None
    if gecen is None:
        nabiz = ('<div class="nabiz sessiz">HENUZ VERI GELMEDI — uzantiyi '
                 'yenile (brave://extensions) ve sahibinden sekmesini '
                 'F5 ile tazele</div>')
    elif gecen <= 150:
        nabiz = ('<div class="nabiz canli">CANLI — son tur ' + str(gecen) +
                 ' sn once geldi</div>')
    else:
        dk = gecen // 60
        nabiz = ('<div class="nabiz sessiz">SESSIZ — ' + str(dk) +
                 ' dakikadir veri yok. Sekme kapanmis, uzanti durmus veya '
                 'sayfa challenge ekraninda olabilir.</div>')

    hafiza_ilan = hafiza_skor = "?"
    k = kopru()
    if k:
        try:
            hafiza_ilan, hafiza_skor = k.durum()
        except Exception:
            pass
    try:
        with db_baglan() as con:
            db_toplam = con.execute(
                "SELECT COUNT(*) FROM oto_ilan").fetchone()[0]
            son_ilanlar = con.execute(
                "SELECT ilan_id, baslik, fiyat, marka, seri, il, ilk_gorme "
                "FROM oto_ilan ORDER BY ilk_gorme DESC, rowid DESC LIMIT 12"
            ).fetchall()
    except Exception:
        db_toplam, son_ilanlar = "?", []

    tg_gonderildi = tg_doping = 0
    if oto_tg is not None:
        try:
            d = oto_tg.durum()
            tg_gonderildi = d.get("gonderildi", 0)
            tg_doping = d.get("atlanan_doping", 0)
        except Exception:
            pass

    p = ['<!doctype html><html lang="tr"><head><meta charset="utf-8">',
         '<meta http-equiv="refresh" content="10">',
         '<title>Oto Kelepir — Canli Durum</title><style>', _CSS,
         '</style></head><body>',
         '<h1>Oto Kelepir — uzanti toplayicisi</h1>',
         '<div class="alt">Baslangic ', _sayac["baslangic"].replace("T", " "),
         ' &middot; bu sayfa 10 saniyede bir kendini yeniler</div>',
         nabiz, _bekci_satiri(), _saatlik_ozet(), '<div class="kutular">',
         _kutu("Tur (sunucu)", _sayac["istek"]),
         _kutu("Sayfada", _sayac.get("son_sayfada") or 0),
         _kutu("Yeni ilan", _sayac["yeni"]),
         _kutu("Zengin semaya", _sayac["zengin_yeni"]),
         _kutu("Telegram", tg_gonderildi),
         _kutu("Doping atlandi", tg_doping),
         _kutu("Hafiza ilan", hafiza_ilan),
         _kutu("Skorlanabilir", hafiza_skor),
         '</div>']
    if _sayac.get("kayip") or _sayac.get("bozuk"):
        p.append('<div class="nabiz sessiz">DIKKAT: ' +
                 str(_sayac.get("kayip", 0)) + ' gonderim sayfa tarafinda '
                 'basarisiz oldu, ' + str(_sayac.get("bozuk", 0)) +
                 ' istek bozuk geldi. Log: sunucu.log</div>')

    p.append('<h2>Son turlar</h2><table><tr><th>Saat</th>'
             '<th>Sayfa turu</th><th>Sekme</th><th>Yol</th>'
             '<th>Sayfada</th><th>Yeni</th><th>Guncel</th>'
             '<th>Zengin yeni</th></tr>')
    if _gecmis:
        for g in reversed(_gecmis):
            p.append('<tr><td>' + g["saat"] + '</td><td>' + str(g["tur"]) +
                     '</td><td class="s">' +
                     ("arkada" if g.get("gizli") else "onde") +
                     '</td><td class="s">' + str(g.get("yol") or "?") +
                     '</td><td>' + str(g["sayfada"]) + '</td><td class="' +
                     ("y" if g["yeni"] else "s") + '">' + str(g["yeni"]) +
                     '</td><td class="s">' + str(g["guncel"]) +
                     '</td><td class="' + ("y" if g["z_yeni"] else "s") +
                     '">' + str(g["z_yeni"]) + '</td></tr>')
    else:
        p.append('<tr><td colspan="8" class="s">henuz tur yok</td></tr>')
    p.append('</table>')

    p.append('<h2>Son goren ilanlar</h2><table><tr><th>Saat</th>'
             '<th>Arac</th><th>Fiyat</th><th>Sehir</th><th></th></tr>')
    for iid, baslik, fiyat, marka, seri, il, ilk in son_ilanlar:
        saat = (ilk or "")[11:19]
        arac = (" ".join(x for x in (marka, seri) if x)
                or (baslik or ""))[:46]
        tl = ("{:,.0f}".format(fiyat).replace(",", ".")
              if fiyat else "")
        p.append('<tr><td>' + saat + '</td><td>' + _kacar(arac) +
                 '</td><td>' + tl + ' TL</td><td>' + _kacar(il or "") +
                 '</td><td><a href="https://www.sahibinden.com/ilan/' +
                 str(iid) + '/detay" target="_blank">ilan</a></td></tr>')
    if not son_ilanlar:
        p.append('<tr><td colspan="5" class="s">henuz ilan yok</td></tr>')
    p.append('</table><div class="alt">Ham sayaclar: '
             '<a href="/durum">/durum</a></div></body></html>')
    return "".join(p)


class Isleyici(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cevap(self, kod, govde, tip="text/plain; charset=utf-8"):
        veri = govde.encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", tip)
        self.send_header("Content-Length", str(len(veri)))
        # Uzanti kaynagindan gelen istekler icin
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # 08.10.2026 — Private Network Access: https://sahibinden.com
        # sayfasindan 127.0.0.1'e istek atilabilmesi icin Chrome bu
        # basligi sart kosuyor. Boylece content script sunucuya
        # DOGRUDAN POST edebilir; service worker tek yol olmaktan
        # cikar (olcum: worker mesaji 20 dakika koptu, 13 tur kayboldu).
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()
        self.wfile.write(veri)

    def do_OPTIONS(self):
        self._cevap(204, "")

    def do_GET(self):
        if self.path.startswith("/durum"):
            with db_baglan() as con:
                toplam = con.execute(
                    "SELECT COUNT(*) FROM oto_ilan").fetchone()[0]
            ek = {}
            k = kopru()
            if k:
                try:
                    n, skor = k.durum()
                    ek = {"hafiza_ilan": n, "hafiza_skorlanabilir": skor}
                except Exception:
                    pass
            # ── BEKCI FRENI ─────────────────────────────────────────
            # Uzantidaki ESKI bekci kodu kararini bu uctaki
            # "son_gorulme"ye gore veriyor ve ayar.json'u okumuyor.
            # PX bloguna girdigimizde o kod sayfayi bes dakikada bir
            # yeniden yuklemeye calisti — challenge ekraninda yenileme
            # HICBIR ISE YARAMAZ (PX kendi gecmiyor, olculdu) ve blogu
            # derinlestirir. Fren acikken bu uc "sayfa az once
            # goruldu" der, boylece eski kod da susar. Gercek deger
            # "son_gorulme_gercek" alaninda; panel ve izleyici onu
            # kullanir, yani olcum KORUNUR.
            fren = False
            try:
                _ay = json.loads(
                    (ROOT / "ayar.json").read_text(encoding="utf-8"))
                fren = _ay.get("bekci_yenileme") is False
            except Exception:
                pass
            if fren:
                ek["son_gorulme_gercek"] = _sayac.get("son_gorulme")
                ek["bekci_frenlendi"] = True
                ek["son_gorulme"] = datetime.now().isoformat(
                    timespec="seconds")
            if oto_tg is not None:
                ek["telegram"] = oto_tg.durum()
            ek["su_seviyesi"] = _su_seviyesi
            ek["bekci"] = _bekci or None
            self._cevap(200, json.dumps(
                {**_sayac, "db_toplam": toplam, **ek},
                ensure_ascii=False, indent=1),
                "application/json; charset=utf-8")
        elif self.path.startswith("/ayar"):
            # Uzantinin CANLI ayarlari. content.js her turda bunu okur,
            # boylece deney/tempo degisikligi icin uzantiyi yeniden
            # yuklemek GEREKMEZ (bir gunde alti kez kullaniciya
            # "uzantiyi yenile" dedikten sonra eklendi).
            try:
                govde = (ROOT / "ayar.json").read_text(encoding="utf-8")
            except Exception:
                govde = json.dumps({"durdur": False, "aralik_min_sn": 50,
                                    "aralik_max_sn": 80,
                                    "varyant_mod": "duz", "ofsetler": [0],
                                    "sayfa_boyu": 50})
            self._cevap(200, govde, "application/json; charset=utf-8")
        elif self.path in ("/", "/panel", "/index.html"):
            self._cevap(200, panel_html(), "text/html; charset=utf-8")
        else:
            self._cevap(404, "yok — panel icin /")

    def _govde(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_POST(self):
        if self.path.startswith("/nabiz"):
            # Uzanti bekcisi: dakikada bir "sekme duruyor mu" raporu.
            try:
                d = self._govde()
            except Exception as e:
                self._cevap(400, f"bozuk: {str(e)[:60]}")
                return
            _bekci.update(d)
            _bekci["zaman"] = datetime.now().isoformat(timespec="seconds")
            if d.get("eylem") and d["eylem"] != "izliyor":
                print(f"{datetime.now():%H:%M:%S}  [bekci] {d['eylem']} "
                      f"(sekme={d.get('sekme')}, donmus={d.get('donmus')}, "
                      f"sessiz={d.get('sessiz_sn')} sn)")
            # 08.10.2026 — BEKCIYE GERCEGI SOYLE. Veri yolu dogrudan
            # POST'a tasindiginda service worker artik mesaj gormuyor,
            # dolayisiyla kendi tuttugu "son veri" kaydi hic guncellenmez
            # ve bekci sessizlik sanip sekmeyi her 5 dakikada bir bosuna
            # yeniliyordu (olcum: "sekme yenilendi (356 sn sessizdi)"
            # oysa turlar akiyordu). Veri gercekten gelip gelmedigini
            # bilen tek yer SUNUCU; karari besleyen sayi da buradan gider.
            gecen = None
            sg = _sayac.get("son_gorulme")
            if sg:
                try:
                    gecen = int((datetime.now()
                                 - datetime.fromisoformat(sg))
                                .total_seconds())
                except Exception:
                    gecen = None
            self._cevap(200, json.dumps({"sunucu_sessiz_sn": gecen}),
                        "application/json; charset=utf-8")
            return
        if not self.path.startswith("/ilan"):
            print(f"{datetime.now():%H:%M:%S}  [? bilinmeyen POST] "
                  f"{self.path[:60]}")
            self._cevap(404, "yok")
            return
        try:
            veri = self._govde()
            kartlar = veri.get("kartlar") or []
        except Exception as e:
            # Sessiz 400 YOK: eskiden bozuk istek hic loglanmiyordu ve
            # "veri neden gelmedi" sorusu cevaplanamiyordu.
            _sayac["bozuk"] = _sayac.get("bozuk", 0) + 1
            print(f"{datetime.now():%H:%M:%S}  [BOZUK ISTEK] "
                  f"{type(e).__name__}: {str(e)[:90]}")
            self._cevap(400, f"bozuk istek: {str(e)[:80]}")
            return
        ozet = veri.get("ozet") or {}

        # ── KATEGORI YONLENDIRME ────────────────────────────────────
        # PC bileseni verisi otomobil DB'sine KARISMAZ; dogrudan
        # apex_predator'un motoruna gider.
        kat = (veri.get("kategori") or ozet.get("kategori")
               or ("pc" if "masaustu-donanim" in str(ozet.get("url", ""))
                   else "otomobil"))
        if kat == "pc":
            ham_pc = veri.get("html") or ""
            k = pc_kopru()
            if not k:
                self._cevap(200, "pc koprusu kapali")
                return
            try:
                with _kopru_kilit:
                    tavan = 5
                    try:
                        _a = json.loads((ROOT / "ayar.json")
                                        .read_text(encoding="utf-8"))
                        tavan = int(_a.get("pc_tg_tavan") or 5)
                    except Exception:
                        pass
                    yazilan, yeni_s, toplam = k.html_isle(ham_pc, tavan)
            except Exception as e:
                print(f"  [pc hatasi] {str(e)[:120]}")
                self._cevap(500, "pc isleme hatasi")
                return
            _sayac["pc_istek"] = _sayac.get("pc_istek", 0) + 1
            _sayac["pc_yazilan"] = _sayac.get("pc_yazilan", 0) + yazilan
            _sayac["pc_son_gorulme"] = datetime.now().isoformat(
                timespec="seconds")
            print(f"{datetime.now():%H:%M:%S}  [PC] tur="
                  f"{ozet.get('tur', '?'):>4}  sayfada={toplam:3}  "
                  f"yeni={yeni_s:3}  DB'ye yazilan={yazilan:3}")
            self._cevap(200, f"pc yeni={yeni_s} yazilan={yazilan}")
            return

        yeni, guncel = ilan_yaz(kartlar, veri.get("kaynak") or "uzanti")
        if ozet.get("challenge"):
            # Sayfa challenge ekraninda. PX kendiliginden GECMIYOR
            # (olculdu, kullanici teyit etti) -> elle gecilmesi gerekir.
            _sayac["challenge"] = _sayac.get("challenge", 0) + 1
            _sayac["son_challenge"] = datetime.now().isoformat(
                timespec="seconds")
            print(f"{datetime.now():%H:%M:%S}  [CHALLENGE] sayfa dogrulama "
                  f"ekraninda (tur={ozet.get('tur')}) — ELLE GECILMESI "
                  f"gerekiyor, PX kendi gecmiyor")
            self._cevap(200, "challenge kaydedildi")
            return
        if ozet.get("kayip"):
            # Sayfa tarafi "onceki N gonderim basarisiz oldu" diyor.
            # 08.10'da bu gorunmedigi icin 13 tur sessizce kayboldu.
            print(f"{datetime.now():%H:%M:%S}  [KAYIP] sayfa tarafinda "
                  f"{ozet['kayip']} gonderim basarisiz olmus — "
                  f"son hata: {str(ozet.get('son_hata'))[:90]}")
            _sayac["kayip"] = _sayac.get("kayip", 0) + int(ozet["kayip"])

        # ── Zengin sema (oto_hafiza.db) — kelepir skorlamasinin kaynagi ──
        global _ilk_tur_gecti, _su_seviyesi
        z_yeni = z_guncel = 0
        tg_atilan = tg_ozet = tg_doping = 0
        ham = veri.get("html") or ""
        pi_adet, pi_ilk3, pi_hash = sayfa_parmak_izi(ham)
        if pi_hash:
            global _son_parmak, _ayni_sayac
            vr = (ozet or {}).get("varyant") or "?"
            if pi_hash == _son_parmak:
                _ayni_sayac += 1
                print(f"{datetime.now():%H:%M:%S}  [BAYAT?] icerik ONCEKI "
                      f"TURLA AYNI ({_ayni_sayac}. kez ust uste) "
                      f"varyant={vr} hash={pi_hash}")
            else:
                print(f"{datetime.now():%H:%M:%S}  [TAZELENDI] icerik "
                      f"{_ayni_sayac} bayat turdan sonra degisti "
                      f"— DEGISTIREN VARYANT: {vr}")
                _ayni_sayac = 0
                _son_parmak = pi_hash
        if ham:
            k = kopru()
            if k:
                yeni_ilanlar = []
                with _kopru_kilit:
                    try:
                        z_yeni, z_guncel = k.html_isle(ham, yeni_ilanlar)
                    except Exception as e:
                        print(f"  [kopru hatasi] {str(e)[:120]}")
                # ── Telegram: ilk kez gorulen ilanlari kanala dusur ──
                if oto_tg is not None and yeni_ilanlar:
                    try:
                        tg_atilan, tg_ozet, tg_doping = \
                            oto_tg.ilanlari_bildir(
                                yeni_ilanlar, su_seviyesi=_su_seviyesi,
                                ilk_tur=not _ilk_tur_gecti)
                    except Exception as e:
                        print(f"  [tg hatasi] {str(e)[:120]}")
                # Su seviyesi yukselir (doping ayrimi bir sonraki turda
                # bu esige gore yapilir).
                for il in yeni_ilanlar:
                    try:
                        _su_seviyesi = max(_su_seviyesi,
                                           int(il.get("ilan_id") or 0))
                    except Exception:
                        pass
            _ilk_tur_gecti = True
            try:
                HAM_DOSYA.write_text(ham, encoding="utf-8")   # teshis kopyasi
                # 08.10.2026 TESHIS: ayni adrese atilan isteklere site IKI
                # farkli cevap donduruyor. 50 kartlik cevaplar DONMUS
                # (ayni hash dakikalarca tekrarliyor, ilk gorulduklerinde
                # 50 ilan birden "yeni" sayiliyor); 51 kartlik cevaplar
                # akici ve tur basina 1 yeni ilan veriyor. Ikisini ayri
                # dosyalara kaydedip karsilastirabilmek icin kart
                # sayisina gore saklaniyor (sahibinden'e EKSTRA ISTEK YOK,
                # veri zaten elimizde).
                (ROOT / f"_sayfa_{pi_adet}.html").write_text(
                    ham, encoding="utf-8")
            except Exception:
                pass

        _sayac["istek"] += 1
        _sayac["ilan"] += len(kartlar)
        _sayac["yeni"] += yeni
        _sayac["guncel"] += guncel
        _sayac["zengin_yeni"] += z_yeni
        _sayac["zengin_guncel"] += z_guncel
        _gecmis.append({
            "saat": datetime.now().strftime("%H:%M:%S"),
            "tur": ozet.get("tur", "?"),
            "sayfada": ozet.get("sayfada", len(kartlar)),
            "yeni": yeni, "guncel": guncel,
            "z_yeni": z_yeni, "z_guncel": z_guncel,
            "gizli": bool(ozet.get("gizli")),
            "yol": "dogrudan" if "dogrudan" in (veri.get("kaynak") or "")
                   else "worker",
        })
        _uzun_gecmis.append((datetime.now(), yeni, z_yeni, tg_atilan))
        try:                                # kalici gun kaydi
            yeni_dosya = not TUR_CSV.exists()
            with TUR_CSV.open("a", encoding="utf-8") as f:
                if yeni_dosya:
                    f.write("zaman,sayfa_turu,sekme,yol,sayfada,yeni,"
                            "guncel,zengin_yeni,zengin_guncel,tg,doping,"
                            "kayip,ilk_id,son_id,adres,kart_adet,"
                            "icerik_hash,ayni_ust_uste,varyant,tetik\n")
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S},"
                        f"{ozet.get('tur', '')},"
                        f"{'arkada' if ozet.get('gizli') else 'onde'},"
                        f"{'dogrudan' if 'dogrudan' in (veri.get('kaynak') or '') else 'worker'},"
                        f"{ozet.get('sayfada', len(kartlar))},{yeni},"
                        f"{guncel},{z_yeni},{z_guncel},{tg_atilan},"
                        f"{tg_doping},{ozet.get('kayip', 0)},"
                        f"{ozet.get('ilk_id', '') or pi_ilk3.split(',')[0]},"
                        f"{ozet.get('son_id', '')},"
                        f"\"{str(ozet.get('url', ''))[:120]}\","
                        f"{pi_adet},{pi_hash},{_ayni_sayac},"
                        f"{ozet.get('varyant', '')},"
                        f"{ozet.get('tetik', '')}\n")
        except Exception as e:
            print(f"  [csv yazilamadi] {str(e)[:80]}")
        # Supheli tur: olculen hiz dakikada ~3.7 yeni kayit; bir turda
        # 20'den fazlasi gelmesi ya adres degisimi ya da sitenin farkli
        # bir liste anlik goruntusu demektir. Kaniti ANINDA yaz.
        if yeni > 20:
            print(f"           ^ SUPHELI TUR: adres={ozet.get('url')} "
                  f"ilk_id={ozet.get('ilk_id')} "
                  f"son_id={ozet.get('son_id')}")
        _sayac["son_tur"] = ozet.get("tur")
        _sayac["son_gorulme"] = datetime.now().isoformat(timespec="seconds")
        _sayac["son_sayfada"] = ozet.get("sayfada")
        # Nabiz: bos tur da loglanir, boylece "calisiyor ama yeni ilan yok"
        # ile "durmus" ayirt edilir.
        print(f"{datetime.now():%H:%M:%S}  tur={ozet.get('tur', '?'):>4}  "
              f"sayfada={ozet.get('sayfada', len(kartlar)):3}  "
              f"yeni={yeni:3d}  guncel={guncel:3d}  "
              f"| zengin +{z_yeni:3d}/~{z_guncel:3d}  "
              f"(toplam yeni={_sayac['yeni']})" +
              (f"  | tg {tg_atilan}" +
               (f"+{tg_ozet} ozet" if tg_ozet else "") +
               (f" (doping {tg_doping} atlandi)" if tg_doping else "")
               if tg_atilan or tg_ozet or tg_doping else ""))
        self._cevap(200, f"yeni={yeni} guncel={guncel} "
                         f"zengin={z_yeni}/{z_guncel}")

    def log_message(self, *a):
        pass        # kendi log'umuzu basiyoruz


def main():
    db_hazirla()
    _su_seviyesi_oku()
    if oto_tg is not None:
        d = oto_tg.durum()
        print(f"  TELEGRAM   : {'ACIK' if d['acik'] else 'KAPALI'} "
              f"(yeni ilan akisi, kelepir avcisi degil)")
    print("=" * 60)
    print("OTO KELEPIR — YEREL SUNUCU")
    print("=" * 60)
    print(f"  PANEL      : http://127.0.0.1:{PORT}/   <- buradan takip et")
    print(f"  ham sayac  : http://127.0.0.1:{PORT}/durum")
    print(f"  DB         : {DB_FILE.name}")
    print("  sahibinden'e HICBIR istek atmaz; sadece uzantidan veri alir.")
    print()
    print("  1) Brave: brave://extensions -> Gelistirici modu ->")
    print("     'Paketlenmemis yukle' -> oto_kelepir/uzanti")
    print("  2) sahibinden otomobil listesini bir sekmede ACIK BIRAK")
    print("  3) Uzantinin ciktisi: sekmede F12 -> Console -> [oto-kelepir]")
    print("=" * 60)
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Isleyici).serve_forever()
    except KeyboardInterrupt:
        print("\nkapatildi.")
        sys.exit(0)


if __name__ == "__main__":
    main()
