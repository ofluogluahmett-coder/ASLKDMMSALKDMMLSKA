"""
ayar.json uzerinde ac/kapat — baslatma betiginin cagirdigi yardimci.

NEDEN AYRI DOSYA: baslat_toplayici.ps1 bu isi PowerShell icine gomulu
python kodu ile yapiyordu ve iki kez kirildi:
  1) here-string ('@...'@) + Turkce karakter -> PowerShell dizge hatasi
  2) string birlestirme + backtick-n -> "SyntaxError: '(' was never
     closed" (toplayici acilmadi, sessizce gecildi)
Kodu dosyaya almak her iki tuzagi da ortadan kaldiriyor. Betik artik
sadece "py -3.12 toplayici_ayar.py ac" diyor.

KULLANIM
    py -3.12 toplayici_ayar.py ac      # durdur=False, bekci=True
    py -3.12 toplayici_ayar.py kapat   # durdur=True  (acil durdurma)
    py -3.12 toplayici_ayar.py durum   # mevcut ayarlari yaz
"""
import json
import sys
from pathlib import Path

AYAR = Path(__file__).parent / "ayar.json"


def oku():
    return json.loads(AYAR.read_text(encoding="utf-8"))


def yaz(d):
    AYAR.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main():
    komut = (sys.argv[1] if len(sys.argv) > 1 else "durum").lower()
    try:
        d = oku()
    except Exception as e:
        print(f"ayar.json okunamadi: {e}")
        return 1

    if komut == "ac":
        d["durdur"] = False
        d["bekci_yenileme"] = True
        for k in ("_durdur_sebep", "_bekci_kapali_sebep"):
            d.pop(k, None)
        yaz(d)
        print("toplayici ACIK (durdur=False, bekci_yenileme=True)")
    elif komut in ("kapat", "dur"):
        d["durdur"] = True
        yaz(d)
        print("toplayici KAPALI (durdur=True)")
    elif komut == "durum":
        gorunur = {k: v for k, v in d.items() if not k.startswith("_")}
        print(json.dumps(gorunur, ensure_ascii=False, indent=2))
    else:
        print(f"bilinmeyen komut: {komut}  (ac | kapat | durum)")
        return 2
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
