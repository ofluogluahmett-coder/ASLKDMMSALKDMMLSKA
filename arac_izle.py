"""
IZLEYICI — ELLE gezilen Brave'i pasif dinler. Hic istek ATMAZ, hic
navigasyon YAPMAZ. Sadece "gercek insan oturumu ne yapiyor?" sorusunu
cevaplar.

NEDEN
  06.10.2026: ayni IP'de kullanici ELLE /otomobil'de serbest geziyor,
  bizim uc oturumumuz 2. istekte "Access to this page has been denied"
  yiyor. Parmak izi denetimi temiz cikti. Demek ki fark DAVRANISTA.
  Bu alet farki olcer:
    1. Sayfa KENDI arka planda hangi istekleri atiyor? (XHR/fetch)
       -> Liste sayfasi kendi kendine yeni ilan sorguluyorsa, biz de
          ayni dogal istegi kullanabiliriz: hem taze hem normal gorunur.
    2. Istekler hangi cache basliklariyla gidiyor? (304 aliyor mu,
       yoksa her seferinde tam indirme mi?)
    3. Oturumda hangi cerezler var? (SADECE ISIM — deger YAZILMAZ)

GIZLILIK
  Kullanicinin kendi profiline baglanir. Bu alet SADECE sahibinden.com
  isteklerinin URL/metot/durum/cache bilgisini ve cerez ISIMLERINI yazar.
  Cerez degerleri, form verileri, sayfa icerigi KAYDEDILMEZ.

KULLANIM
  1. Brave'i TAMAMEN kapat.
  2. baslat_brave_izle.bat  (kendi profilin + debug portu)
  3. py -3.12 arac_izle.py            # dinlemeye baslar
  4. Brave'de NORMAL gez: listeyi yenile, siralamayi degistir, bir ilana
     gir, geri don. Ne yaptigini ekranda gorursun.
  5. Ctrl+C -> ozet.
"""
import json
import sys
import time
import urllib.request
from collections import Counter

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 9222
BASE = "http://127.0.0.1:%d" % PORT
SURE = int(sys.argv[sys.argv.index("--sure") + 1]) if "--sure" in sys.argv else 600

ILGI = "sahibinden.com"
# Cache davranisini anlatan basliklar
CACHE_BASLIK = ("cache-control", "if-none-match", "if-modified-since",
                "etag", "last-modified", "age", "x-cache", "cf-cache-status",
                "pragma")


def _ws_ac():
    import websocket
    with urllib.request.urlopen(BASE + "/json/list", timeout=8) as r:
        hedefler = json.loads(r.read().decode("utf-8"))
    sayfa = next((t for t in hedefler if t.get("type") == "page"), None)
    if not sayfa:
        print("[DUR] Acik sayfa bulunamadi.")
        sys.exit(2)
    print("IZLENEN SEKME: %s" % (sayfa.get("title") or "")[:60])
    print("URL          : %s" % (sayfa.get("url") or "")[:80])
    return websocket.create_connection(sayfa["webSocketDebuggerUrl"],
                                       timeout=5, suppress_origin=True)


def main():
    try:
        ws = _ws_ac()
    except Exception as e:
        print("[DUR] Brave'e baglanilamadi (port %d): %s" % (PORT, e))
        print("      Brave'i kapat, baslat_brave_izle.bat ile ac.")
        sys.exit(2)

    mid = [0]

    def cagir(metot, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": metot,
                            "params": params or {}}))

    cagir("Network.enable")
    cagir("Page.enable")

    print("-" * 78)
    print("DINLEMEDE — Brave'de normal gez. Ctrl+C ile ozet.")
    print("-" * 78)

    tipler = Counter()
    durumlar = Counter()
    xhr_urller = Counter()
    belge_istek = 0
    nav = 0
    bitis = time.time() + SURE
    istek_tip = {}

    try:
        while time.time() < bitis:
            try:
                ws.settimeout(2.0)
                ham = ws.recv()
            except Exception:
                continue
            try:
                m = json.loads(ham)
            except Exception:
                continue
            metot = m.get("method")
            p = m.get("params", {})

            if metot == "Network.requestWillBeSent":
                istek = p.get("request", {})
                url = istek.get("url", "")
                if ILGI not in url:
                    continue
                tip = p.get("type", "?")
                tipler[tip] += 1
                istek_tip[p.get("requestId")] = (tip, url)
                basliklar = {k.lower(): v for k, v in
                             (istek.get("headers") or {}).items()}
                cache_bilgi = {k: basliklar[k] for k in CACHE_BASLIK
                               if k in basliklar}
                if tip == "Document":
                    belge_istek += 1
                    print("\n[SAYFA] %s" % url[:100])
                    if cache_bilgi:
                        print("        giden cache basliklari: %s" % cache_bilgi)
                    else:
                        print("        giden cache basligi YOK (normal gezinme)")
                elif tip in ("XHR", "Fetch"):
                    kisa = url.split("?")[0][-70:]
                    xhr_urller[kisa] += 1
                    print("  [%s] %s" % (tip, url[:110]))
                    if cache_bilgi:
                        print("        %s" % cache_bilgi)

            elif metot == "Network.responseReceived":
                r = p.get("response", {})
                url = r.get("url", "")
                if ILGI not in url:
                    continue
                tip, _ = istek_tip.get(p.get("requestId"), ("?", ""))
                durum = r.get("status")
                durumlar["%s:%s" % (tip, durum)] += 1
                if tip in ("Document", "XHR", "Fetch"):
                    bas = {k.lower(): v for k, v in
                           (r.get("headers") or {}).items()}
                    cb = {k: bas[k] for k in CACHE_BASLIK if k in bas}
                    print("        -> %s %s  %s" % (durum, tip, cb or ""))

            elif metot == "Page.frameNavigated":
                if not (p.get("frame", {}).get("parentId")):
                    nav += 1

    except KeyboardInterrupt:
        pass

    # Cerez ISIMLERI (deger yazilmaz)
    try:
        cagir("Network.getAllCookies")
        ws.settimeout(5.0)
        isimler = []
        for _ in range(30):
            try:
                m = json.loads(ws.recv())
            except Exception:
                break
            if "result" in m and "cookies" in m.get("result", {}):
                isimler = sorted({c["name"] for c in m["result"]["cookies"]
                                  if ILGI in c.get("domain", "")})
                break
    except Exception:
        isimler = []

    print("\n" + "=" * 78)
    print("OZET")
    print("=" * 78)
    print("Ust duzey navigasyon : %d" % nav)
    print("Document istegi      : %d" % belge_istek)
    print("Istek tipleri        : %s" % dict(tipler))
    print("Durum kodlari        : %s" % dict(durumlar))
    if xhr_urller:
        print("\nSAYFANIN KENDI XHR/FETCH ISTEKLERI (en siktan):")
        for u, n in xhr_urller.most_common(12):
            print("  %3dx  %s" % (n, u))
        print("\n>>> Bunlardan biri ilan listesi donduruyorsa, botun")
        print(">>> HTML'i cache kirarak tekrar istemesine gerek yok.")
    else:
        print("\nSayfa kendi XHR/fetch istegi ATMADI — liste tamamen")
        print("sunucu tarafinda uretilip HTML ile geliyor.")
    if isimler:
        print("\nsahibinden cerezleri (SADECE ISIM): %s" % ", ".join(isimler))
    print("=" * 78)


if __name__ == "__main__":
    main()
