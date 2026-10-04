"""OTO KELEPIR — TEK KAYNAK DURUM RAPORU.

TASARIM KURALLARI (hepsi sahada yanilmaktan ogrenildi):

1. VERI sorulari VERITABANINDAN cevaplanir, olay sorulari log'dan.
   Sebep: kod her SAYFADAN sonra commit eder ama "DB: yeni=..." ozetini
   TUR SONUNDA basar. Tur kesilirse ozet yazilmaz, veri ise kaydedilmistir.
   Log'dan ilan saymak "0 ilan toplandi" gibi TAMAMEN yanlis sonuc verdi.

2. SADECE ORTALAMA GOSTERME. "PX: 2, ~45 dk'da bir" demek, kullanicinin
   az once yasadigi olayi gorunmez kilar ve rapora guveni bitirir. Her
   olay ZAMAN CIZELGESINDE tek tek gorunur.

3. OLU ZAMANI AYIR. PX ekraninda gecen sure botun calistigi sure degildir.
   "51 dakikada 179 ilan" icinde 24 dakikalik bekleme varsa bu soylenmeli.

4. Log olaylari tekillestirilir (bot ayni olayi 2 kez yaziyor).

Kullanim:
    python durum.py          → son 60 dakika
    python durum.py 180      → son 180 dakika
    python durum.py 60 -t    → sadece zaman cizelgesi
"""
import glob
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

KOK = Path(__file__).resolve().parent
DB = KOK / "oto_hafiza.db"
PREFS = KOK / "brave_oto_profile_login" / "Default" / "Preferences"
SECURE_PREFS = KOK / "brave_oto_profile_login" / "Default" / "Secure Preferences"

arglar = [a for a in sys.argv[1:] if not a.startswith("-")]
PENCERE_DK = int(arglar[0]) if arglar else 60
SADECE_CIZELGE = "-t" in sys.argv

RE_Z = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# (ad, regex, cizelgede_goster, simge)
OLAY_TIPLERI = [
    ("bot basladi",  re.compile(r"OTO KELEPIR AVCISI"),                 True,  "▶"),
    ("PX geldi",     re.compile(r"PX EKRANI|PX/CF EKRANI|PERIMETERX CAPTCHA"), True, "✋"),
    ("PX gecildi",   re.compile(r"PX ekrani gecildi|Ekran gecildi|Captcha cozuldu"), True, "✓"),
    ("timeout",      re.compile(r"Chromedriver cevap vermiyor"),        True,  "⏱"),
    ("tarayici oldu", re.compile(r"tarayici kapanmis|Driver olmus"),    True,  "✖"),
    ("login duvari", re.compile(r"LOGIN DUVARI|GIRIS DUVARI"),          True,  "🔐"),
    ("brave acildi", re.compile(r"Brave baslatiliyor"),                 False, "↻"),
    ("tur",          re.compile(r"Tur #(\d+)"),                         False, "·"),
]
# Bot'un calismasini DURDURAN olaylar (kesinti sayilir)
KESINTI = {"PX geldi", "timeout", "tarayici oldu", "login duvari"}


def _ps(komut):
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", komut],
                              capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception:
        return ""


def _proc(ad):
    """Process sayisi + en eski baslama. durum.py kendini saymaz."""
    f = f"Get-Process {ad} -ErrorAction SilentlyContinue | Where-Object {{ $_.Id -ne {os.getpid()} }}"
    n = _ps(f"@({f}).Count")
    t = _ps(f"{f} | Sort-Object StartTime | Select-Object -First 1 | "
            f"ForEach-Object {{ $_.StartTime.ToString('yyyy-MM-dd HH:mm:ss') }}")
    try:
        return int(n or 0), t
    except ValueError:
        return 0, t


def olaylari_oku(esik):
    """Log'u oku, tekillestir, (zaman, tip, simge) listesi dondur."""
    sat = []
    for dosya in glob.glob(str(KOK / "oto*.log*")):
        try:
            with open(dosya, encoding="utf-8", errors="replace") as f:
                for s in f:
                    m = RE_Z.match(s)
                    if m:
                        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                        if t >= esik:
                            sat.append((t, s))
        except Exception:
            pass
    sat.sort(key=lambda x: x[0])
    olaylar = []
    son = {}
    for t, s in sat:
        for ad, rx, goster, simge in OLAY_TIPLERI:
            if not rx.search(s):
                continue
            # tekillestirme: ayni tip 90sn icinde tekrarlanmaz ('tur' haric)
            if ad != "tur" and ad in son and (t - son[ad]).total_seconds() < 90:
                continue
            son[ad] = t
            olaylar.append((t, ad, simge, goster))
            break
    return olaylar, (sat[-1][0] if sat else None)


def main():
    simdi = datetime.now()
    esik = simdi - timedelta(minutes=PENCERE_DK)
    olaylar, son_log = olaylari_oku(esik)

    # ── PX bekleme surelerini eslestir ────────────────────────────
    px_cift = []
    acik_px = None
    for t, ad, _, _ in olaylar:
        if ad == "PX geldi":
            acik_px = t
        elif ad == "PX gecildi" and acik_px:
            px_cift.append((acik_px, t, (t - acik_px).total_seconds() / 60))
            acik_px = None
    px_bekleyen = acik_px          # hala acik bir PX var mi
    olu_dk = sum(d for _, _, d in px_cift)
    if px_bekleyen:
        olu_dk += (simdi - px_bekleyen).total_seconds() / 60

    if not SADECE_CIZELGE:
        print("=" * 70)
        print(f"  OTO KELEPIR — DURUM   ({simdi:%d.%m.%Y %H:%M:%S})"
              f"   |  son {PENCERE_DK} dk")
        print("=" * 70)

        # ── SU AN ──────────────────────────────────────────────────
        py_n, py_t = _proc("python")
        br_n, _ = _proc("brave")
        print("\n[ SU AN ]")
        if py_n == 0:
            print("  ⛔ BOT CALISMIYOR")
        else:
            ek = ""
            if py_t:
                try:
                    d = (simdi - datetime.strptime(py_t, "%Y-%m-%d %H:%M:%S"))
                    ek = f"  —  {d.total_seconds()/60:.0f} dk ayakta (basladi {py_t[11:]})"
                except Exception:
                    pass
            print(f"  bot calisiyor{ek}")
            if py_n > 1:
                print("  ⚠️ BIRDEN FAZLA INSTANCE — profil bozulabilir!")
        print(f"  brave process: {br_n}")

        if px_bekleyen:
            bekl = (simdi - px_bekleyen).total_seconds() / 60
            print(f"  ✋ PX EKRANI ACIK — {px_bekleyen:%H:%M:%S}'den beri "
                  f"{bekl:.0f} DAKIKADIR SENI BEKLIYOR")
        elif son_log:
            sess = (simdi - son_log).total_seconds() / 60
            if sess > 8:
                print(f"  ⚠️ {sess:.0f} dakikadir log'a hicbir sey yazilmadi")
            else:
                print(f"  tarama akiyor (son log {sess:.0f} dk once)")

        # son kesintiden beri
        kesintiler = [t for t, ad, _, _ in olaylar if ad in KESINTI]
        if kesintiler:
            gecen = (simdi - kesintiler[-1]).total_seconds() / 60
            print(f"  son kesintiden beri: {gecen:.0f} dakika")
        else:
            print(f"  ✅ bu pencerede HIC kesinti yok ({PENCERE_DK} dk)")

        # ── VERI (VERITABANI) ──────────────────────────────────────
        print("\n[ VERI — kaynak: veritabani ]")
        try:
            c = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=10)
            toplam = c.execute("SELECT COUNT(*) FROM ilan").fetchone()[0]
            t_esik = esik.strftime("%Y-%m-%dT%H:%M:%S")
            n = c.execute("SELECT COUNT(*) FROM ilan WHERE ilk_gorulme >= ?",
                          (t_esik,)).fetchone()[0]
            son_ilan = c.execute("SELECT MAX(ilk_gorulme) FROM ilan").fetchone()[0]
            c.close()
            print(f"  toplam ilan        : {toplam:,}")
            print(f"  son {PENCERE_DK} dk'da     : {n} yeni ilan"
                  f"   ({10*n/PENCERE_DK:.1f} ilan/10dk ham)")
            # olu zaman duzeltmesi
            calisan = max(PENCERE_DK - olu_dk, 1)
            if olu_dk >= 1:
                print(f"  ├─ PX'te beklenen  : {olu_dk:.0f} dk  (bot bu surede DURDU)")
                print(f"  └─ gercek calisma  : {calisan:.0f} dk"
                      f"   → {10*n/calisan:.1f} ilan/10dk NET")
            if son_ilan:
                g = (simdi - datetime.fromisoformat(son_ilan)).total_seconds() / 60
                uyari = "   ⚠️" if g > 15 else ""
                print(f"  en son ilan        : {son_ilan[11:19]} ({g:.0f} dk once){uyari}")
        except Exception as e:
            print(f"  veritabani okunamadi: {e}")

        # ── SAYIMLAR ───────────────────────────────────────────────
        print(f"\n[ OLAY SAYIMI — son {PENCERE_DK} dk ]")
        for ad, _, _, _ in OLAY_TIPLERI:
            k = sum(1 for _, a, _, _ in olaylar if a == ad)
            if ad == "tur":
                print(f"  {'tur':<16s} {k:>4}")
                continue
            ek = ""
            if k and ad in ("PX geldi", "timeout"):
                ek = f"   (~{PENCERE_DK/k:.0f} dk'da bir)"
            print(f"  {ad:<16s} {k:>4}{ek}")
        if px_cift:
            sur = [d for _, _, d in px_cift]
            print(f"\n  PX'te gecen sure: toplam {sum(sur):.0f} dk, "
                  f"en uzun {max(sur):.0f} dk, ortalama {sum(sur)/len(sur):.0f} dk")

    # ── ZAMAN CIZELGESI ────────────────────────────────────────────
    print(f"\n[ ZAMAN CIZELGESI — son {PENCERE_DK} dk ]")
    gosterilecek = [(t, ad, sim) for t, ad, sim, g in olaylar if g]
    if not gosterilecek:
        print("  (kayda deger olay yok — kesintisiz calisti)")
    else:
        onceki = None
        for t, ad, sim in gosterilecek[-30:]:
            ara = f"  (+{(t-onceki).total_seconds()/60:.0f} dk)" if onceki else ""
            etiket = ad
            # PX gecildi ise ne kadar surdugu
            for b, s, d in px_cift:
                if ad == "PX gecildi" and s == t:
                    etiket = f"PX gecildi  [{d:.0f} dk surdu]"
            print(f"  {t:%H:%M:%S}  {sim}  {etiket}{ara}")
            onceki = t
        if px_bekleyen:
            print(f"  {simdi:%H:%M:%S}  …  PX HALA ACIK, bekliyor")

    if SADECE_CIZELGE:
        return

    # ── SAGLIK ─────────────────────────────────────────────────────
    print("\n[ SAGLIK ]")
    if PREFS.exists():
        kb = PREFS.stat().st_size / 1024
        durum = "normal" if kb < 1024 else "⚠️ SISME BUGU GERI GELMIS (uc guncellendi mi?)"
        print(f"  Preferences   : {kb:.1f} KB   {durum}")
    env = KOK / ".env"
    if env.exists():
        ham = env.read_text(encoding="utf-8", errors="replace")
        aktif = any(l.strip().startswith("PROXY_LIST=")
                    and l.split("=", 1)[1].strip() for l in ham.splitlines())
        print(f"  proxy         : {'AKTIF' if aktif else 'KAPALI'}")
    if SECURE_PREFS.exists():
        h = SECURE_PREFS.read_text(encoding="utf-8", errors="replace")
        if "proxy" in h.lower() and re.search(r"\d{1,3}(\.\d{1,3}){3}", h):
            print("  ⚠️ Secure Preferences'ta HAYALET PROXY izi var")


if __name__ == "__main__":
    main()
