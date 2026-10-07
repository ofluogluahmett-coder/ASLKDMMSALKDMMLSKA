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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
DB_FILE = ROOT / "oto_tarama.db"
PORT = 8765

_sayac = {"istek": 0, "ilan": 0, "yeni": 0, "guncel": 0,
          "baslangic": datetime.now().isoformat(timespec="seconds")}


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
            self._cevap(200, json.dumps(
                {**_sayac, "db_toplam": toplam}, ensure_ascii=False, indent=1),
                "application/json; charset=utf-8")
        else:
            self._cevap(200, "oto kelepir yerel sunucu — /durum")

    def do_POST(self):
        if not self.path.startswith("/ilan"):
            self._cevap(404, "yok")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            ham = self.rfile.read(n).decode("utf-8")
            veri = json.loads(ham)
            kartlar = veri.get("kartlar") or []
        except Exception as e:
            self._cevap(400, f"bozuk istek: {str(e)[:80]}")
            return
        yeni, guncel = ilan_yaz(kartlar, veri.get("kaynak") or "uzanti")
        _sayac["istek"] += 1
        _sayac["ilan"] += len(kartlar)
        _sayac["yeni"] += yeni
        _sayac["guncel"] += guncel
        print(f"{datetime.now():%H:%M:%S}  gelen={len(kartlar):3d}  "
              f"yeni={yeni:3d}  guncel={guncel:3d}  "
              f"(toplam yeni={_sayac['yeni']})")
        self._cevap(200, f"yeni={yeni} guncel={guncel}")

    def log_message(self, *a):
        pass        # kendi log'umuzu basiyoruz


def main():
    db_hazirla()
    print("=" * 60)
    print("OTO KELEPIR — YEREL SUNUCU")
    print("=" * 60)
    print(f"  dinleniyor : http://127.0.0.1:{PORT}")
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
