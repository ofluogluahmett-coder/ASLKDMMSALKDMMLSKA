"""
PX RAPORU — "gunde kac kere PX geliyor, kac kere ELLE mudahale gerekti?"

Kullanici olcutu (06.10.2026): gunde EN FAZLA 3-4 elle mudahale. Bu alet
tarama log'undan tam bunu hesaplar; gozle satir saymak yok.

KULLANIM
  py -3.12 arac_px_rapor.py                 # px_gece.log
  py -3.12 arac_px_rapor.py test_x.log      # baska log

OKUNAN SATIRLAR (oto_tarama.py'nin bastigi bicimler)
  '[TUR n] HH:MM:SS ilan=.. yeni=..'            -> basarili tur
  '[TUR n] [!] CHALLENGE (k. ust uste)'          -> PX/CF olayi
  'ISINMA: ana sayfa gezildi'                    -> yeni oturum (toparlanma)
  '[TUR n] [!] 0 ilan (challenge DEGIL)'         -> bos sayfa (ayri ariza)

HUKUM
  Devre kesici challenge'i COZMEYE calismaz: oturumu birakir, bekler, temiz
  profille doner. Yani bir challenge'in ARDINDAN basarili tur geliyorsa o
  olay ELLE MUDAHALE GEREKTIRMEDI. Elle mudahale = challenge'dan sonra bir
  daha hic basarili tur gelmemesi (bot orada kalmis).
"""
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
TUR_RE = re.compile(r"^\[TUR (\d+)\] (\d\d:\d\d:\d\d) ilan=(\d+) yeni=(\d+)"
                    r".*?en_yeni_id=(\d+)")
CHAL_RE = re.compile(r"^\[TUR (\d+)\] \[!\] CHALLENGE \((\d+)\. ust uste\)")
BOS_RE = re.compile(r"^\[TUR (\d+)\] \[!\] 0 ilan")
ISINMA_RE = re.compile(r"^ISINMA: ana sayfa gezildi")


def _sn(hhmmss):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return h * 3600 + m * 60 + s


def main():
    yol = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "px_gece.log")
    if not yol.exists():
        print(f"[DUR] log bulunamadi: {yol}")
        sys.exit(2)

    turlar = []          # (tur_no, saniye, ilan, yeni)
    challenge = []       # (tur_no, kacinci_ust_uste, satir_sirasi)
    bos = []
    isinma = 0
    olaylar = []         # sirali: ('tur', n) / ('chal', n)

    with open(yol, encoding="utf-8", errors="replace") as f:
        for satir in f:
            satir = satir.rstrip("\n")
            m = TUR_RE.match(satir)
            if m:
                turlar.append((int(m.group(1)), _sn(m.group(2)),
                               int(m.group(3)), int(m.group(4)),
                               int(m.group(5))))
                olaylar.append(("tur", int(m.group(1))))
                continue
            m = CHAL_RE.match(satir)
            if m:
                challenge.append((int(m.group(1)), int(m.group(2))))
                olaylar.append(("chal", int(m.group(1))))
                continue
            if BOS_RE.match(satir):
                bos.append(satir)
                continue
            if ISINMA_RE.match(satir):
                isinma += 1

    if not turlar:
        print("Hic basarili tur yok. Log basi:")
        print(open(yol, encoding="utf-8", errors="replace").read()[:600])
        sys.exit(1)

    # Gecen sure (saat gecisini tolere et)
    ilk, son = turlar[0][1], turlar[-1][1]
    gecen = son - ilk
    if gecen < 0:
        gecen += 24 * 3600
    saat = gecen / 3600.0 if gecen else 0.0

    # PX'siz en uzun seri
    seri = en_uzun = 0
    for tip, _ in olaylar:
        if tip == "tur":
            seri += 1
            en_uzun = max(en_uzun, seri)
        else:
            seri = 0

    # Toparlanma: her challenge'dan SONRA basarili tur geldi mi?
    toparlanan = 0
    for i, (tip, _) in enumerate(olaylar):
        if tip != "chal":
            continue
        if any(t == "tur" for t, _ in olaylar[i + 1:]):
            toparlanan += 1
    elle = len(challenge) - toparlanan

    # ── TAZELIK: en_yeni_id turlar arasinda ILERLIYOR mu? ──
    # Bayat liste belirtisi: id ayni kalirken turlar gecer. Yogun saatte
    # /otomobil'de 2-3 dakika hic yeni ilan girmemesi gercekci degil.
    ilerledi = donuk = 0
    en_uzun_donma_sn = 0
    donma_basi = None
    onceki_id = None
    for t in turlar:
        yid = t[4]
        if onceki_id is None or yid > onceki_id:
            ilerledi += 1
            if donma_basi is not None:
                sure = t[1] - donma_basi
                if sure < 0:
                    sure += 24 * 3600
                en_uzun_donma_sn = max(en_uzun_donma_sn, sure)
                donma_basi = None
        else:
            donuk += 1
            if donma_basi is None:
                donma_basi = t[1]
        onceki_id = max(onceki_id or 0, yid)
    if donma_basi is not None:   # log sonunda hala donuk
        sure = turlar[-1][1] - donma_basi
        if sure < 0:
            sure += 24 * 3600
        en_uzun_donma_sn = max(en_uzun_donma_sn, sure)

    yeni_toplam = sum(t[3] for t in turlar)
    print("=" * 66)
    print(f"PX RAPORU — {yol.name}")
    print("=" * 66)
    print(f"Sure                 : {saat:.2f} saat ({gecen // 60} dk)")
    print(f"Basarili tur         : {len(turlar)}")
    print(f"Toplanan yeni ilan   : {yeni_toplam}")
    print(f"Yeni oturum (isinma) : {isinma}")
    print(f"Bos sayfa turu       : {len(bos)}")
    print("-" * 66)
    print(f"TAZELIK  ilerleyen tur : {ilerledi}/{len(turlar)}"
          f"  ({100.0 * ilerledi / len(turlar):.0f}%)")
    print(f"         donuk tur     : {donuk}")
    print(f"         en uzun donma : {en_uzun_donma_sn // 60} dk "
          f"{en_uzun_donma_sn % 60} sn")
    if en_uzun_donma_sn >= 180:
        print("         -> BAYAT LISTE belirtisi (yogun saatte 3+ dk donma)")
    print("-" * 66)
    print(f"CHALLENGE olayi      : {len(challenge)}")
    print(f"  kendi toparladi    : {toparlanan}")
    print(f"  ELLE MUDAHALE      : {elle}")
    print(f"PX'siz en uzun seri  : {en_uzun} tur")
    print("-" * 66)
    if saat >= 0.2:
        ch_saat = len(challenge) / saat
        el_saat = elle / saat
        print(f"Challenge / saat     : {ch_saat:.2f}  -> gunde ~{ch_saat * 24:.1f}")
        print(f"Elle mudahale / saat : {el_saat:.2f}  -> gunde ~{el_saat * 24:.1f}")
        hedef = el_saat * 24
        print("-" * 66)
        if hedef <= 4:
            print(f"HUKUM: HEDEF TUTUYOR (gunde ~{hedef:.1f} elle mudahale <= 4)")
        else:
            print(f"HUKUM: HEDEF TUTMUYOR (gunde ~{hedef:.1f} elle mudahale > 4)")
            print("       Siradaki kol: tur periyodunu buyut (TEMEL_MIN/MAX),")
            print("       eylul ortasindaki temiz donem 180-300 sn kullaniyordu.")
    else:
        print("(Sure cok kisa — hukum icin en az ~15 dk gerekir.)")
    print("=" * 66)


if __name__ == "__main__":
    main()
