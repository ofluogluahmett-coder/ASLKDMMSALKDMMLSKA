"""OTO KELEPIR AVCISI — KOHORT TAKIBI (dogrulama dongusu).

AMAC
  Botun "kelepir" dedigi ilanlar gercekten kelepir miydi? En guvenilir
  olcut: birkac gun icinde SATILMIS olmalari.

NEDEN KONTROL GRUBU SART
  "Adaylarimizin %50'si gitti" tek basina HICBIR SEY soylemez. Eger
  rastgele bir ilanin da %50'si ayni surede gidiyorsa model sifir deger
  katiyor demektir. Bu yuzden her aday icin AYNI BUCKET'tan, sapmasi ~0
  olan ve km'si en yakin bir ilan da takibe alinir (eslestirilmis kontrol).

  Olculen sey: aday_gitme_orani - kontrol_gitme_orani
  Bu fark pozitif ve anlamliysa model is goruyor.

KRITIK KURAL — BLOCK'U SATIS SAYMA
  Sayfa cekilemediyse, PX/block ekrani geldiyse veya sayfa taninamadiysa
  sonuc 'belirsiz'dir ve SAYIMA GIRMEZ. Block'u "ilan satildi" diye
  kaydetmek tum olcumu coper. Bu yuzden siniflandirici muhafazakar:
  emin olmadiginda 'belirsiz' der ve ornegi kalibrasyon icin saklar.

HIZ
  Gunde ~20 kontrol (10 aday + 10 kontrol). Detay worker gecmiste yogun
  istekle block yemisti; burada hacim kasten dusuk tutuluyor ve kontroller
  sadece molalarda, block sinyali yokken yapilir.

Bu modul AG KULLANMAZ. Sayfayi bot ceker, siniflandirmayi burasi yapar —
boylece siniflandirici ag olmadan test edilebilir.
"""

import re
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "oto_hafiza.db"
ORNEK_DIZIN = ROOT / "takip_ornek"      # taninamayan sayfalar buraya

# ── AYARLAR ───────────────────────────────────────────────
GUNLUK_KONTROL_TAVANI = 20        # aday + kontrol toplam
KONTROL_GUNLERI = [2, 4, 7, 11, 15]   # ilan eklendikten kac gun sonra bakilir
TAKIP_BITIS_GUN = 15
KONTROL_SAPMA_BANDI = (-8.0, 8.0)     # kontrol grubu icin "normal fiyatli"

SEMA = """
CREATE TABLE IF NOT EXISTS takip (
    ilan_id       TEXT PRIMARY KEY,
    kohort        TEXT NOT NULL,      -- 'aday' | 'kontrol'
    es_ilan_id    TEXT,               -- kontrol ise hangi adayla eslesti
    seviye        TEXT,               -- VURGUN/FIRSAT/IZLE (kontrolde NULL)
    sapma         REAL,
    akran_sapma   REAL,
    katman        TEXT,
    bucket        TEXT,
    bucket_n      INTEGER,
    r2            REAL,
    guven         REAL,
    marka         TEXT,
    seri          TEXT,
    yil           INTEGER,
    km            INTEGER,
    fiyat         REAL,
    kimden        TEXT,
    url           TEXT,
    eklendi       TEXT NOT NULL,
    son_kontrol   TEXT,
    kontrol_say   INTEGER DEFAULT 0,
    durum         TEXT DEFAULT 'canli',   -- 'canli' | 'gitti' | 'bitti'
    gitti_tarih   TEXT,
    omur_gun      REAL,
    belirsiz_say  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_takip_durum  ON takip(durum);
CREATE INDEX IF NOT EXISTS ix_takip_kohort ON takip(kohort);
"""


def _simdi():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _gun_farki(a, b):
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400
    except Exception:
        return 0.0


def sema_kur(con):
    con.executescript(SEMA)
    con.commit()


# ── SAYFA SINIFLANDIRICI ──────────────────────────────────
# Muhafazakar: sadece KESIN isaretlerde karar verir.

CANLI_ISARET = (
    "classifiedinfolist",          # detay sayfasinin ana bilgi listesi
    "classifieddetailtitle",
)
GITTI_ISARET = (
    "yayından kaldırıl", "yayindan kaldiril",
    "ilan yayında değil", "ilan yayinda degil",
    "bu ilan yayında değil", "yayında olmayan",
    "ilan bulunamadı", "ilan bulunamadi",
    "aradığınız ilan", "aradiginiz ilan",
    "ilan silinmiş", "ilan silinmis",
    "sayfa bulunamadı", "sayfa bulunamadi",
)
BLOCK_ISARET = (
    "px-captcha", "perimeterx", "px-cloud",
    "just a moment", "attention required", "bir saniye",
    "erişim engellendi", "erisim engellendi",
    "access denied", "robot",
)


def sayfa_durumu(html, title=""):
    """Detay sayfasini siniflandirir.

    Donus: 'canli' | 'gitti' | 'block' | 'belirsiz'

    'belirsiz' ve 'block' SAYIMA GIRMEZ — tekrar denenir.
    """
    if not html:
        return "belirsiz"
    h = html.lower()
    t = str(title or "").lower()

    # 1) Block once bakilir — block sayfasi da 'ilan bulunamadi' diyebilir
    for m in BLOCK_ISARET:
        if m in t or m in h[:4000]:
            return "block"
    # 2) Cok kucuk sayfa = soft block
    if len(html) < 20_000:
        return "block"
    # 3) Kesin olum isaretleri
    for m in GITTI_ISARET:
        if m in t or m in h:
            return "gitti"
    # 4) Kesin canli isaretleri
    for m in CANLI_ISARET:
        if m in h:
            return "canli"
    return "belirsiz"


# ── KOHORT KAYDI ──────────────────────────────────────────
def kohort_ekle(con, adaylar, skorlar, tavan=40):
    """Yeni adaylari ve ESLESTIRILMIS kontrollerini takibe alir.

    Kontrol secimi: ayni bucket, sapmasi KONTROL_SAPMA_BANDI icinde,
    km'si adaya en yakin olan ilan. Boylece marka/seri/yil/km sabit
    tutulur, degisen tek sey 'ucuz mu degil mi' olur.
    """
    sema_kur(con)
    mevcut = {r[0] for r in con.execute("SELECT ilan_id FROM takip")}

    # bucket -> normal fiyatli ilanlar (kontrol havuzu)
    havuz = {}
    for s in skorlar:
        lo, hi = KONTROL_SAPMA_BANDI
        if lo <= s["sapma"] <= hi:
            havuz.setdefault((s["katman"], s["bucket"]), []).append(s)

    from kelepir import I
    eklenen_a = eklenen_k = 0
    now = _simdi()

    def _yaz(s, kohort, es=None):
        r = s["r"]
        con.execute("""
            INSERT OR IGNORE INTO takip
              (ilan_id, kohort, es_ilan_id, seviye, sapma, akran_sapma, katman,
               bucket, bucket_n, r2, guven, marka, seri, yil, km, fiyat,
               kimden, url, eklendi)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (s["ilan_id"], kohort, es, s.get("seviye"), s["sapma"],
             s.get("akran_sapma"), s["katman"], s["bucket"], s["n"], s["r2"],
             s["guven"], r[I["marka"]], r[I["seri"]], r[I["yil"]], r[I["km"]],
             r[I["fiyat"]], r[I["kimden"]], r[I["url"]], now))

    for s in adaylar:
        if eklenen_a >= tavan:
            break
        if s["ilan_id"] in mevcut:
            continue
        _yaz(s, "aday")
        mevcut.add(s["ilan_id"])
        eklenen_a += 1

        # eslestirilmis kontrol
        aday_km = s["r"][I["km"]]
        secenek = [c for c in havuz.get((s["katman"], s["bucket"]), [])
                   if c["ilan_id"] not in mevcut]
        if secenek:
            es = min(secenek, key=lambda c: abs(c["r"][I["km"]] - aday_km))
            _yaz(es, "kontrol", s["ilan_id"])
            mevcut.add(es["ilan_id"])
            eklenen_k += 1

    con.commit()
    return eklenen_a, eklenen_k


# ── KONTROL KUYRUGU ───────────────────────────────────────
def kontrol_kuyrugu(con, tavan=GUNLUK_KONTROL_TAVANI):
    """Bugun bakilmasi gereken ilanlar. Aday ve kontrol DENGELI cekilir —
    biri taranip digeri taranmazsa karsilastirma bozulur."""
    sema_kur(con)
    now = _simdi()
    aday, kontrol = [], []
    for row in con.execute("""
            SELECT ilan_id, kohort, url, eklendi, son_kontrol, kontrol_say
            FROM takip WHERE durum='canli' ORDER BY COALESCE(son_kontrol, eklendi)"""):
        iid, kohort, url, eklendi, son, say = row
        yas = _gun_farki(eklendi, now)
        if yas > TAKIP_BITIS_GUN:
            con.execute("UPDATE takip SET durum='bitti' WHERE ilan_id=?", (iid,))
            continue
        # sira gelmis mi?
        gereken = [g for g in KONTROL_GUNLERI if g <= yas]
        if len(gereken) <= (say or 0):
            continue
        if son and _gun_farki(son, now) < 1.0:
            continue          # gunde bir defadan fazla bakma
        (aday if kohort == "aday" else kontrol).append((iid, kohort, url))
    con.commit()

    yari = max(1, tavan // 2)
    return aday[:yari] + kontrol[:yari]


def sonuc_yaz(con, ilan_id, durum):
    """durum: 'canli' | 'gitti' | 'block' | 'belirsiz'"""
    now = _simdi()
    if durum == "gitti":
        row = con.execute("SELECT eklendi FROM takip WHERE ilan_id=?",
                          (ilan_id,)).fetchone()
        omur = _gun_farki(row[0], now) if row else None
        con.execute("""UPDATE takip SET durum='gitti', gitti_tarih=?, omur_gun=?,
                       son_kontrol=?, kontrol_say=COALESCE(kontrol_say,0)+1
                       WHERE ilan_id=?""", (now, omur, now, ilan_id))
    elif durum == "canli":
        con.execute("""UPDATE takip SET son_kontrol=?,
                       kontrol_say=COALESCE(kontrol_say,0)+1
                       WHERE ilan_id=?""", (now, ilan_id))
    else:   # block / belirsiz — SAYILMAZ, sadece not dusulur
        con.execute("""UPDATE takip SET belirsiz_say=COALESCE(belirsiz_say,0)+1
                       WHERE ilan_id=?""", (ilan_id,))
    con.commit()


# ── RAPOR ─────────────────────────────────────────────────
def _oran(gitti, toplam):
    return 100.0 * gitti / toplam if toplam else 0.0


def _wilson(k, n, z=1.96):
    """Wilson guven araligi — kucuk orneklemde normal yaklasim yaniltir."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z*z/n
    merkez = (p + z*z/(2*n)) / d
    yari = z * ((p*(1-p)/n + z*z/(4*n*n)) ** 0.5) / d
    return (100*max(0, merkez-yari), 100*min(1, merkez+yari))


def rapor(con):
    sema_kur(con)
    sat = []
    top = con.execute("SELECT COUNT(*) FROM takip").fetchone()[0]
    if not top:
        return "takip tablosu bos — henuz kohort eklenmemis."

    sat.append("=" * 74)
    sat.append("KOHORT TAKIP RAPORU")
    sat.append("=" * 74)

    # yeterince kontrol edilmis olanlar (en az 1 gecerli kontrol)
    for kohort in ("aday", "kontrol"):
        n = con.execute("SELECT COUNT(*) FROM takip WHERE kohort=?",
                        (kohort,)).fetchone()[0]
        olculen = con.execute(
            "SELECT COUNT(*) FROM takip WHERE kohort=? AND "
            "(kontrol_say>0 OR durum='gitti')", (kohort,)).fetchone()[0]
        gitti = con.execute(
            "SELECT COUNT(*) FROM takip WHERE kohort=? AND durum='gitti'",
            (kohort,)).fetchone()[0]
        lo, hi = _wilson(gitti, olculen)
        sat.append(f"  {kohort:<8} takipte {n:>4}  olculen {olculen:>4}  "
                   f"gitti {gitti:>4}  =%{_oran(gitti, olculen):>5.1f}  "
                   f"[%{lo:.0f}-%{hi:.0f}]")

    a_n = con.execute("SELECT COUNT(*) FROM takip WHERE kohort='aday' AND "
                      "(kontrol_say>0 OR durum='gitti')").fetchone()[0]
    a_g = con.execute("SELECT COUNT(*) FROM takip WHERE kohort='aday' AND "
                      "durum='gitti'").fetchone()[0]
    k_n = con.execute("SELECT COUNT(*) FROM takip WHERE kohort='kontrol' AND "
                      "(kontrol_say>0 OR durum='gitti')").fetchone()[0]
    k_g = con.execute("SELECT COUNT(*) FROM takip WHERE kohort='kontrol' AND "
                      "durum='gitti'").fetchone()[0]

    sat.append("")
    if a_n >= 20 and k_n >= 20:
        fark = _oran(a_g, a_n) - _oran(k_g, k_n)
        alo, ahi = _wilson(a_g, a_n)
        klo, khi = _wilson(k_g, k_n)
        cakisma = not (alo > khi or klo > ahi)
        sat.append(f"  FARK (aday - kontrol) : %{fark:+.1f}")
        if cakisma:
            sat.append("  → guven araliklari CAKISIYOR: henuz anlamli fark YOK, "
                       "ornek buyutulmeli")
        else:
            sat.append("  → guven araliklari AYRIK: fark anlamli, model is goruyor")
    else:
        sat.append(f"  (karar icin her kohortta >=20 olculen gerekiyor; "
                   f"su an aday={a_n}, kontrol={k_n})")

    sat.append("")
    sat.append("  SEVIYE BAZINDA (sadece aday kohortu)")
    for sv in ("VURGUN", "FIRSAT", "IZLE"):
        n = con.execute("SELECT COUNT(*) FROM takip WHERE seviye=? AND "
                        "(kontrol_say>0 OR durum='gitti')", (sv,)).fetchone()[0]
        g = con.execute("SELECT COUNT(*) FROM takip WHERE seviye=? AND "
                        "durum='gitti'", (sv,)).fetchone()[0]
        if n:
            sat.append(f"    {sv:<8} {g:>3}/{n:<3} =%{_oran(g, n):>5.1f}")

    sat.append("")
    sat.append("  SAPMA BANDI BAZINDA (aday)")
    for lo, hi in [(-50, -35), (-35, -30), (-30, -25), (-25, -20)]:
        n = con.execute("SELECT COUNT(*) FROM takip WHERE kohort='aday' AND "
                        "sapma>? AND sapma<=? AND (kontrol_say>0 OR durum='gitti')",
                        (lo, hi)).fetchone()[0]
        g = con.execute("SELECT COUNT(*) FROM takip WHERE kohort='aday' AND "
                        "sapma>? AND sapma<=? AND durum='gitti'",
                        (lo, hi)).fetchone()[0]
        if n:
            sat.append(f"    %{lo}..%{hi:<4} {g:>3}/{n:<3} =%{_oran(g, n):>5.1f}")

    omur = [r[0] for r in con.execute(
        "SELECT omur_gun FROM takip WHERE kohort='aday' AND omur_gun IS NOT NULL")]
    if omur:
        sat.append("")
        sat.append(f"  giden adaylarin medyan omru: {statistics.median(omur):.1f} gun")

    bel = con.execute("SELECT COUNT(*) FROM takip WHERE belirsiz_say>0").fetchone()[0]
    if bel:
        sat.append(f"\n  not: {bel} ilanda en az bir belirsiz/block sonucu alindi "
                   f"(sayima girmedi)")
    return "\n".join(sat)


# ── CLI ───────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    con = sqlite3.connect(DB_FILE)
    komut = sys.argv[1] if len(sys.argv) > 1 else "rapor"

    if komut == "ekle":
        import kelepir
        rows, modeller, skorlar, adaylar = kelepir.calistir()
        tavan = int(sys.argv[2]) if len(sys.argv) > 2 else 40
        a, k = kohort_ekle(con, adaylar, skorlar, tavan)
        print(f"takibe alindi: {a} aday + {k} eslestirilmis kontrol")
        print(rapor(con))
    elif komut == "kuyruk":
        for iid, kohort, url in kontrol_kuyrugu(con):
            print(f"{kohort:<8} {iid}  {url}")
    else:
        print(rapor(con))
    con.close()
