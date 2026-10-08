"""
DENEY RAPORU — onbellek kirma deneyinin sonucunu olcer.

SORU: sahibinden bize ~5-6 dakikada bir tazelenen DONDURULMUS bir liste
veriyor. jQuery'nin kendi cache:false parametresi ("_=<ms>") bu
onbellegi kiriyor mu?

YONTEM: content.js turlari donusumlu olarak (a) duz adres, (b) "_=<ms>"
ekli adres ile cekiyor. Sunucu her turun kart ID dizisinin hash'ini
tutuyor. Bir varyantin "tazeleyici" sayilmasi icin o turda icerik
onceki turdan FARKLI olmalidir.

OLCUT — DIKKAT: ilk surumde olcut "icerik degisti mi" idi ve YANLISTI;
iki varyant da %100 cikti. Sebep: iki onbellek dugumu arasinda
salindigimiz icin icerik HER turda degisiyor, ama degisen icerik taze
olmak zorunda degil.
DOGRU OLCUT: tur basina TAZE ilan = (yeni - doping). Yani ID su
seviyesinin bandinda, ilk kez gorulen ilan sayisi.

08.10.2026 SONUCU: cachebust 20.8 taze/tur, duz 0.8 taze/tur -> 26 KAT.
Turlar donusumlu kosuldugu icin piyasa hizi iki varyant icin ayniydi.

Kullanim: py -3.12 arac_deney_rapor.py [tur_gunlugu.csv]
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def oku(yol):
    satir = []
    with open(yol, encoding="utf-8") as f:
        for x in csv.DictReader(f):
            if x.get("zaman") and x.get("icerik_hash"):
                satir.append(x)
    satir.sort(key=lambda x: x["zaman"])
    return satir


def main():
    yol = sys.argv[1] if len(sys.argv) > 1 else "tur_gunlugu.csv"
    if not Path(yol).exists():
        print(f"{yol} yok.")
        return
    satir = oku(yol)
    deneyli = [x for x in satir if (x.get("varyant") or "").strip()]
    print("=" * 66)
    print("ONBELLEK KIRMA DENEYI — RAPOR")
    print("=" * 66)
    print(f"kayitli tur: {len(satir)} | deneyli tur: {len(deneyli)}")
    if len(deneyli) < 4:
        print("\nHenuz yeterli veri yok (en az 4 deneyli tur gerekli).")
        return

    # Her varyant icin: tur, yeni, doping, TAZE toplamlari
    say = defaultdict(lambda: [0, 0, 0, 0])   # [tur, yeni, doping, taze]
    print("\nzaman     tur  varyant      yeni  dop  TAZE  icerik")
    onceki_hash = None
    for x in deneyli:
        v = (x["varyant"] or "?").split("+")[0]
        yeni = int(x["yeni"] or 0)
        dop = int(x["doping"] or 0)
        taze = yeni - dop
        s = say[v]
        s[0] += 1
        s[1] += yeni
        s[2] += dop
        s[3] += taze
        isaret = "(ilk)" if onceki_hash is None else (
            "DEGISTI" if x["icerik_hash"] != onceki_hash else "ayni")
        onceki_hash = x["icerik_hash"]
        print(f"{x['zaman'][11:]} {x['sayfa_turu']:>4}  {v:11} "
              f"{yeni:>4} {dop:>4} {taze:>5}  {isaret}")

    print("\n" + "-" * 66)
    print("SONUC — tur basina TAZE ilan (yeni - doping)")
    for v, (t, y, d, z) in sorted(say.items()):
        if t:
            print(f"  {v:11}: {t:3d} tur | yeni {y/t:5.1f} | "
                  f"doping {d/t:5.1f} | TAZE {z/t:5.1f}")

    if "cachebust" in say and "duz" in say:
        cb, dz = say["cachebust"], say["duz"]
        if cb[0] and dz[0]:
            o_cb, o_dz = cb[3] / cb[0], dz[3] / dz[0]
            kat = o_cb / o_dz if o_dz > 0 else float("inf")
            print()
            if o_cb > o_dz * 2:
                print(f"  => CACHEBUST KIRIYOR ({kat:.0f} KAT taze ilan). "
                      f"ayar.json -> varyant_mod='cachebust'")
            elif o_dz > o_cb * 2:
                print("  => BEKLENMEDIK: duz adres daha tazeliyor. "
                      "Deney tekrarlanmali.")
            else:
                print("  => FARK YOK. Onbellek adres bazli DEGIL. "
                      "Ikinci plana gec: ofsetler [0, 50].")

    # Tazelenme araliklari (patlama = yeni >= 20)
    pat = [x for x in satir if int(x["yeni"] or 0) >= 20]
    if len(pat) > 1:
        print("\n" + "-" * 66)
        print("TAZELENME (patlama) ARALIKLARI")
        onc = None
        aralar = []
        for x in pat[-10:]:
            t = datetime.strptime(x["zaman"], "%Y-%m-%d %H:%M:%S")
            if onc:
                dk = (t - onc).total_seconds() / 60
                aralar.append(dk)
                print(f"  {x['zaman'][11:]}  {x['yeni']:>3} yeni   "
                      f"{dk:.1f} dk")
            else:
                print(f"  {x['zaman'][11:]}  {x['yeni']:>3} yeni")
            onc = t
        if aralar:
            print(f"  ortalama ara: {sum(aralar)/len(aralar):.1f} dk  "
                  f"(deney oncesi olcum: ~5.5 dk)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
