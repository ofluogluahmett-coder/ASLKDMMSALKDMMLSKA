"""OTO KELEPIR AVCISI — skorlama motoru (Faz 2).

Bu modul TEK BASINA calisir (python kelepir.py) ve bot tarafindan da
import edilir. Tarayici/ag kullanmaz — sadece DB okur.

24.09.2026 — olculerek kurulan kurallar:

  1. SENTETIK ILAN FILTRESI
     url'inde 'sb2f' gecen 513 satirin %100'u sahte (baslik anahtar kelime
     salatasi, url markasi DB markasiyla tutmuyor, attr_ham bayat).
     Diger 18.597 ilanin HICBIRINDE 'sb2f' yok. Kesin ayrac.

  2. ALMAN PREMIUM MOTOR KODU
     BMW'de %100, Mercedes'te %98 motor_hacim_grup='bilinmiyor' idi — cunku
     '320d' / 'C 200 d' kodlari \\d\\.\\d regex'ine takilmiyor. Motor kodu
     key'e girince Mercedes bucket yayilimi %-19 iyilesti (BMW %-6).

  3. TABAN FILTRESI (%-50)
     Bunun altindaki sapma kelepir degil: veri hatasi veya yem ilan.
     Olcum: sapma <%-50 olan 44 ilanin medyan km'si 196.000, medyan yili
     2016 — hasarli arac bolgesi.

  4. ESIK %-20 (eski: %-35)
     Hedef isabet degil KAPSAM. %-35'te 21 aday vardi ve 6 marka tamamen
     kordu; %-20'de 314 aday ve o markalar acildi. Pazarlik payi %5
     varsayilirsa %-20 havuzunun medyan etkin sapmasi %-30 oluyor.

     DIKKAT: %-20 bandinda boya (%10-15) ve hafif hasar sapmayi tek basina
     aciklayabilir. Bu esik Faz 3'u (detay/boya) opsiyonel olmaktan
     cikarip ON KOSUL yapar.

  5. ROBUST AKRAN KONTROLU
     OLS regresyonu uc degere duyarli. Her aday ayrica AYNI KM BANDINDAKI
     (+-40.000 km) akranlarinin medyaniyla karsilastirilir; ikisi birden
     'ucuz' demezse aday sayilmaz.
"""

import re
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "oto_hafiza.db"

# ── ESIKLER ───────────────────────────────────────────────
SAPMA_ESIK      = -20.0    # aday olmak icin gereken minimum sapma
SAPMA_TABAN     = -50.0    # bunun altinda yem/veri hatasi — ELE
AKRAN_ESIK      = -12.0    # robust kontrol: akran medyanina gore
VURGUN_SAPMA    = -32.0
FIRSAT_SAPMA    = -25.0
VURGUN_GUVEN    = 60.0
FIRSAT_GUVEN    = 50.0

# katman basina minimum bucket buyuklugu ve regresyon kalitesi
MIN_N   = {"L1": 8,    "L2": 10,   "L3": 15}
MIN_R2  = {"L1": 0.15, "L2": 0.20, "L3": 0.25}
KATMAN_GUVEN = {"L1": 30, "L2": 20, "L3": 10}

AKRAN_KM_BANDI  = 40_000
AKRAN_MIN_N     = 4

# ── SENTETIK / COP FILTRESI ───────────────────────────────
SENTETIK_SQL = (
    "url NOT LIKE '%sb2f%' AND ilan_id NOT LIKE '846%' "
    "AND COALESCE(cop_mu,0)=0"
)

RE_BAYRAK = re.compile(
    r"senet|taksit|peşinat|pesinat|kefil|hasarl|pert|kaza|tramer|"
    r"hurda|parçalık|parcalik|gümrüksüz|gumruksuz|yabancı plaka|yabanci plaka",
    re.I,
)

# ── ALMAN PREMIUM MOTOR KODU ──────────────────────────────
RE_BMW      = re.compile(r"\b([1-8]\d{2})\s*([di])\b", re.I)
RE_BMW_X    = re.compile(r"\b(X[1-7])\b", re.I)
RE_MB       = re.compile(r"\b([A-Z]{0,3})\s*(\d{3})\s*(d|CDI|BlueTEC|BlueTec)?\b")
RE_MB_DIZEL = re.compile(r"\b(d|CDI|BlueTEC|BlueTec)\b")


def motor_kodu(marka, model):
    """BMW/Mercedes icin hacim yerine MOTOR KODU dondurur ('320d', '200d').
    Diger markalarda None — onlarda motor_hacim_grup zaten dolu."""
    m = str(model or "")
    mk = str(marka or "").strip().lower()
    if mk == "bmw":
        g = RE_BMW.search(m)
        if g:
            return f"{g.group(1)}{g.group(2).lower()}"
        g = RE_BMW_X.search(m)
        return g.group(1).lower() if g else None
    if mk == "mercedes-benz":
        g = RE_MB.search(m)
        if g:
            dizel = "d" if (g.group(3) or RE_MB_DIZEL.search(m)) else ""
            return f"{g.group(2)}{dizel}"
    return None


# ── YARDIMCILAR ───────────────────────────────────────────
def _n(v):
    return (str(v).strip().lower() or "na") if v is not None else "na"


ALANLAR = ("ilan_id marka seri motor_hacim_grup motor_tipi paket yil "
           "arac_sinifi fiyat km kimden baslik url il ilce model satici_ad "
           "ilk_gorulme").split()
I = {ad: i for i, ad in enumerate(ALANLAR)}


def _hacim(r):
    return motor_kodu(r[I["marka"]], r[I["model"]]) or _n(r[I["motor_hacim_grup"]])


def anahtar(katman, r):
    ma, se, yi = _n(r[I["marka"]]), _n(r[I["seri"]]), r[I["yil"]]
    if katman == "L1":
        return (f"{ma}|{se}|{_hacim(r)}|{_n(r[I['motor_tipi']])}|"
                f"{_n(r[I['paket']])}|{yi}")
    if katman == "L2":
        return f"{ma}|{se}|{_hacim(r)}|{_n(r[I['motor_tipi']])}|{yi}"
    return f"{ma}|{se}|{yi}"


def _regres(v):
    """En kucuk kareler: fiyat = a + b*km. (a, b, r2) veya None."""
    xs = [x[I["km"]] for x in v]
    ys = [x[I["fiyat"]] for x in v]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    sst = sum((y - my) ** 2 for y in ys)
    if sst == 0:
        return None
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    return a, b, 1 - sse / sst


def ilanlari_getir(con, sadece_aktif=True):
    kosul = " AND durum='aktif'" if sadece_aktif else ""
    return con.execute(
        f"SELECT {', '.join(ALANLAR)} FROM ilan "
        f"WHERE {SENTETIK_SQL}{kosul} AND arac_sinifi='ikinci_el' "
        f"  AND fiyat>0 AND km IS NOT NULL AND yil IS NOT NULL"
    ).fetchall()


def modelleri_kur(rows):
    """Her katman icin bucket -> (a, b, r2, n, uyeler) sozlugu."""
    gruplar = {k: {} for k in ("L1", "L2", "L3")}
    for r in rows:
        for k in gruplar:
            gruplar[k].setdefault(anahtar(k, r), []).append(r)

    modeller = {k: {} for k in gruplar}
    for katman, g in gruplar.items():
        for key, v in g.items():
            if len(v) < MIN_N[katman]:
                continue
            res = _regres(v)
            if not res:
                continue
            a, b, r2 = res
            if b >= 0 or r2 < MIN_R2[katman]:
                continue          # km arttikca fiyat artiyorsa bucket bozuk
            modeller[katman][key] = (a, b, r2, len(v), v)
    return modeller


def seviye_belirle(sapma, guven):
    if sapma <= VURGUN_SAPMA and guven >= VURGUN_GUVEN:
        return "VURGUN"
    if sapma <= FIRSAT_SAPMA and guven >= FIRSAT_GUVEN:
        return "FIRSAT"
    return "IZLE"


def skorla(rows, modeller):
    """Tum ilanlari skorlar. Her ilan icin dict dondurur (aday olmayanlar dahil)
    — kontrol grubu secimi icin sapmasi ~0 olanlar da lazim."""
    cikti = []
    for r in rows:
        secilen = None
        for katman in ("L1", "L2", "L3"):
            m = modeller[katman].get(anahtar(katman, r))
            if m:
                secilen = (katman, m)
                break
        if not secilen:
            continue
        katman, (a, b, r2, n, uyeler) = secilen
        bek = a + b * r[I["km"]]
        if bek <= 0:
            continue
        sapma = 100 * (r[I["fiyat"]] - bek) / bek

        akran = [x[I["fiyat"]] for x in uyeler
                 if abs(x[I["km"]] - r[I["km"]]) <= AKRAN_KM_BANDI
                 and x[I["ilan_id"]] != r[I["ilan_id"]]]
        akran_sapma = None
        if len(akran) >= AKRAN_MIN_N:
            med = statistics.median(akran)
            if med > 0:
                akran_sapma = 100 * (r[I["fiyat"]] - med) / med

        guven = r2 * 40 + min(30, n / 2) + KATMAN_GUVEN[katman]
        cikti.append({
            "r": r, "ilan_id": r[I["ilan_id"]], "katman": katman,
            "sapma": sapma, "akran_sapma": akran_sapma, "beklenen": bek,
            "n": n, "r2": r2, "guven": guven,
            "bucket": anahtar(katman, r),
            "bayrak": bool(RE_BAYRAK.search(
                f"{r[I['baslik']]} {r[I['model']]}")),
        })
    return cikti


def adaylari_sec(skorlar):
    """Aday = esigi gecen + taban ustu + bayraksiz + akran onayli."""
    out = []
    for s in skorlar:
        if not (SAPMA_ESIK >= s["sapma"] > SAPMA_TABAN):
            continue
        if s["bayrak"]:
            continue
        a = s["akran_sapma"]
        if a is None or not (AKRAN_ESIK >= a > SAPMA_TABAN):
            continue
        s["seviye"] = seviye_belirle(s["sapma"], s["guven"])
        out.append(s)
    out.sort(key=lambda s: s["sapma"])
    return out


def calistir(db=DB_FILE, sadece_aktif=True):
    con = sqlite3.connect(db)
    try:
        rows = ilanlari_getir(con, sadece_aktif)
        modeller = modelleri_kur(rows)
        skorlar = skorla(rows, modeller)
        return rows, modeller, skorlar, adaylari_sec(skorlar)
    finally:
        con.close()


# ── CLI ───────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    from collections import Counter

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rows, modeller, skorlar, adaylar = calistir()

    print(f"havuz (temiz, aktif, ikinci el) : {len(rows):,}")
    print(f"skorlanabilen                   : {len(skorlar):,} "
          f"(%{100*len(skorlar)/max(len(rows),1):.0f})")
    kc = Counter(s["katman"] for s in skorlar)
    print(f"  L1={kc['L1']:,}  L2={kc['L2']:,}  L3={kc['L3']:,}")
    print(f"ADAY (esik %{SAPMA_ESIK:.0f})             : {len(adaylar):,}")
    sc = Counter(s["seviye"] for s in adaylar)
    for sv in ("VURGUN", "FIRSAT", "IZLE"):
        print(f"  {sv:<8} {sc[sv]:>5,}")

    print(f"\nEN IYI {limit} ADAY")
    print("-" * 100)
    for s in adaylar[:limit]:
        r = s["r"]
        print(f"{s['seviye']:<7} %{s['sapma']:>5.0f} (akran %{s['akran_sapma']:>5.0f})  "
              f"{r[I['fiyat']]:>11,.0f} TL  {r[I['km']]:>7,} km  {r[I['yil']]}  "
              f"{r[I['marka']]} {r[I['seri']]} {str(r[I['model']])[:26]}")
        print(f"        {r[I['ilan_id']]}  {s['katman']} n={s['n']} "
              f"R2={s['r2']:.2f} guven={s['guven']:.0f}  "
              f"{r[I['il']]}/{r[I['ilce']]}  [{r[I['kimden']]}]")
