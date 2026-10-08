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

ROOT = Path(__file__).parent
DB_FILE = ROOT / "oto_tarama.db"
HAM_DOSYA = ROOT / "_canli_sayfa.html"      # teshis icin son liste HTML'i
PORT = 8765

_sayac = {"istek": 0, "ilan": 0, "yeni": 0, "guncel": 0,
          "zengin_yeni": 0, "zengin_guncel": 0,
          "baslangic": datetime.now().isoformat(timespec="seconds")}

# Panel icin tur gecmisi (bellekte, son 25 tur)
_gecmis = deque(maxlen=25)

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

    p = ['<!doctype html><html lang="tr"><head><meta charset="utf-8">',
         '<meta http-equiv="refresh" content="10">',
         '<title>Oto Kelepir — Canli Durum</title><style>', _CSS,
         '</style></head><body>',
         '<h1>Oto Kelepir — uzanti toplayicisi</h1>',
         '<div class="alt">Baslangic ', _sayac["baslangic"].replace("T", " "),
         ' &middot; bu sayfa 10 saniyede bir kendini yeniler</div>',
         nabiz, _bekci_satiri(), '<div class="kutular">',
         _kutu("Tur (sunucu)", _sayac["istek"]),
         _kutu("Sayfada", _sayac.get("son_sayfada") or 0),
         _kutu("Yeni ilan", _sayac["yeni"]),
         _kutu("Zengin semaya", _sayac["zengin_yeni"]),
         _kutu("Hafiza ilan", hafiza_ilan),
         _kutu("Skorlanabilir", hafiza_skor),
         _kutu("Hafif tablo", db_toplam),
         '</div>']

    p.append('<h2>Son turlar</h2><table><tr><th>Saat</th>'
             '<th>Sayfa turu</th><th>Sekme</th>'
             '<th>Sayfada</th><th>Yeni</th><th>Guncel</th>'
             '<th>Zengin yeni</th></tr>')
    if _gecmis:
        for g in reversed(_gecmis):
            p.append('<tr><td>' + g["saat"] + '</td><td>' + str(g["tur"]) +
                     '</td><td class="s">' +
                     ("arkada" if g.get("gizli") else "onde") +
                     '</td><td>' + str(g["sayfada"]) + '</td><td class="' +
                     ("y" if g["yeni"] else "s") + '">' + str(g["yeni"]) +
                     '</td><td class="s">' + str(g["guncel"]) +
                     '</td><td class="' + ("y" if g["z_yeni"] else "s") +
                     '">' + str(g["z_yeni"]) + '</td></tr>')
    else:
        p.append('<tr><td colspan="7" class="s">henuz tur yok</td></tr>')
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
            self._cevap(200, json.dumps(
                {**_sayac, "db_toplam": toplam, **ek},
                ensure_ascii=False, indent=1),
                "application/json; charset=utf-8")
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
            self._cevap(200, "ok")
            return
        if not self.path.startswith("/ilan"):
            self._cevap(404, "yok")
            return
        try:
            veri = self._govde()
            kartlar = veri.get("kartlar") or []
        except Exception as e:
            self._cevap(400, f"bozuk istek: {str(e)[:80]}")
            return
        yeni, guncel = ilan_yaz(kartlar, veri.get("kaynak") or "uzanti")
        ozet = veri.get("ozet") or {}

        # ── Zengin sema (oto_hafiza.db) — kelepir skorlamasinin kaynagi ──
        z_yeni = z_guncel = 0
        ham = veri.get("html") or ""
        if ham:
            k = kopru()
            if k:
                with _kopru_kilit:
                    try:
                        z_yeni, z_guncel = k.html_isle(ham)
                    except Exception as e:
                        print(f"  [kopru hatasi] {str(e)[:120]}")
            try:
                HAM_DOSYA.write_text(ham, encoding="utf-8")   # teshis kopyasi
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
        })
        _sayac["son_tur"] = ozet.get("tur")
        _sayac["son_gorulme"] = datetime.now().isoformat(timespec="seconds")
        _sayac["son_sayfada"] = ozet.get("sayfada")
        # Nabiz: bos tur da loglanir, boylece "calisiyor ama yeni ilan yok"
        # ile "durmus" ayirt edilir.
        print(f"{datetime.now():%H:%M:%S}  tur={ozet.get('tur', '?'):>4}  "
              f"sayfada={ozet.get('sayfada', len(kartlar)):3}  "
              f"yeni={yeni:3d}  guncel={guncel:3d}  "
              f"| zengin +{z_yeni:3d}/~{z_guncel:3d}  "
              f"(toplam yeni={_sayac['yeni']})")
        self._cevap(200, f"yeni={yeni} guncel={guncel} "
                         f"zengin={z_yeni}/{z_guncel}")

    def log_message(self, *a):
        pass        # kendi log'umuzu basiyoruz


def main():
    db_hazirla()
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
