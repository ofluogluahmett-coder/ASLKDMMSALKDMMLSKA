"""
PARMAK IZI DENETIMI — botumuz kendini nerelerden ele veriyor?

SIFIR RISK: sahibinden'e HIC istek atilmaz. Sayfa olarak data: URL kullanilir,
tum olcum tarayicinin kendi icinde yapilir.

NEDEN
  05.10.2026 kaniti: ayni IP, ayni dakika — kullanicinin ELLE actigi Brave
  /otomobil'de serbest geziyor (siralama, ilan detayi, hicbir PX yok); uc ile
  acilan Brave tek duz URL isteginde "Access to this page has been denied"
  yiyor. Yani damga IP'de DEGIL, otomasyon kimligimizde. Ustelik her acilista
  TEMIZ gecici profil kullaniyoruz — yani hatirlanan sey cerez degil,
  her seferinde ayni uretilen PARMAK IZI.

KULLANIM
  py -3.12 arac_parmak_izi.py              # uc ile acilan Brave'i denetle
  py -3.12 arac_parmak_izi.py --port 9222  # ELLE acilmis Brave'e baglanip denetle
                                           # (karsilastirma icin: fark nerede?)
Ciktiyi iki modda alip karsilastir — FARKLI olan her satir bizim izimiz.
"""
import json
import os
import re
import sys

import undetected_chromedriver as uc
from selenium import webdriver

BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
uc.Chrome.__del__ = lambda self: None

# Bilinen otomasyon ele-vermeleri. Her biri PX/Cloudflare sensorlerinin
# gercekten okudugu seyler.
JS = r"""
(function(){
  var r = {};
  function g(f){ try { return f(); } catch(e){ return "HATA:" + e.name; } }

  // 1) En klasik: WebDriver bayragi
  r["navigator.webdriver"]       = g(function(){ return navigator.webdriver; });

  // 2) chromedriver'in sayfaya enjekte ettigi degiskenler (cdc_...)
  r["cdc_ degiskenleri"]         = g(function(){
      return Object.keys(window).filter(function(k){
        return /^[$_]?cdc_|^\$chrome_asyncScriptInfo|^__webdriver|^__selenium|^__fxdriver/.test(k);
      }).join(",") || "yok";
  });
  r["document cdc_"]             = g(function(){
      return Object.keys(document).filter(function(k){ return /cdc_|selenium|webdriver/i.test(k); }).join(",") || "yok";
  });

  // 3) Eklenti/plugin profili — otomasyonda genelde bos olur
  r["plugins.length"]            = g(function(){ return navigator.plugins.length; });
  r["mimeTypes.length"]          = g(function(){ return navigator.mimeTypes.length; });

  // 4) Dil/yerel — IP TR ise burasi da TR olmali
  r["languages"]                 = g(function(){ return navigator.languages.join(","); });
  r["language"]                  = g(function(){ return navigator.language; });
  r["timezone"]                  = g(function(){ return Intl.DateTimeFormat().resolvedOptions().timeZone; });

  // 5) chrome nesnesi ve runtime
  r["window.chrome var mi"]      = g(function(){ return typeof window.chrome; });
  r["chrome.runtime var mi"]     = g(function(){ return typeof (window.chrome||{}).runtime; });
  r["chrome anahtarlari"]        = g(function(){ return Object.keys(window.chrome||{}).join(","); });

  // 6) Izin durumu tutarsizligi (klasik headless/otomasyon testi)
  r["Notification.permission"]   = g(function(){ return Notification.permission; });

  // 7) WebGL kimligi
  r["webgl vendor"]              = g(function(){
      var c=document.createElement("canvas"), gl=c.getContext("webgl");
      var d=gl.getExtension("WEBGL_debug_renderer_info");
      return gl.getParameter(d.UNMASKED_VENDOR_WEBGL);
  });
  r["webgl renderer"]            = g(function(){
      var c=document.createElement("canvas"), gl=c.getContext("webgl");
      var d=gl.getExtension("WEBGL_debug_renderer_info");
      return gl.getParameter(d.UNMASKED_RENDERER_WEBGL);
  });

  // 8) UA ve UA-CH (brands listesi otomasyonda farkli olabilir)
  r["userAgent"]                 = g(function(){ return navigator.userAgent; });
  r["uaData.brands"]             = g(function(){
      return (navigator.userAgentData.brands||[]).map(function(b){ return b.brand+"/"+b.version; }).join(" | ");
  });
  r["platform"]                  = g(function(){ return navigator.platform; });
  r["hardwareConcurrency"]       = g(function(){ return navigator.hardwareConcurrency; });
  r["deviceMemory"]              = g(function(){ return navigator.deviceMemory; });

  // 9) Ekran — otomasyonda sik gorulen "tuhaf" degerler
  r["screen"]                    = g(function(){ return screen.width+"x"+screen.height+" avail "+screen.availWidth+"x"+screen.availHeight; });
  r["pencere ic"]                = g(function(){ return innerWidth+"x"+innerHeight; });
  r["devicePixelRatio"]          = g(function(){ return devicePixelRatio; });
  r["outerHeight-innerHeight"]   = g(function(){ return outerHeight - innerHeight; });

  // 10) Native fonksiyon imzasi bozulmus mu (yama izi)
  r["toString yamasi"]           = g(function(){
      var sorun=[];
      [["permissions.query",navigator.permissions&&navigator.permissions.query],
       ["Function.toString",Function.prototype.toString]].forEach(function(p){
        try { if (p[1] && !/\{\s*\[native code\]\s*\}/.test(Function.prototype.toString.call(p[1]))) sorun.push(p[0]); }
        catch(e){ sorun.push(p[0]+":hata"); }
      });
      return sorun.join(",") || "temiz";
  });

  // 11) CDP acik mi? (Runtime.enable klasik tespiti: Error.stack'e erisim)
  r["CDP izi (stack)"]           = g(function(){
      var sayac=0, e=new Error();
      Object.defineProperty(e,"stack",{ get:function(){ sayac++; return ""; } });
      console.debug(e);
      return sayac>0 ? "VAR (Runtime.enable acik gorunuyor)" : "yok";
  });

  // 12) Brave'e ozgu: navigator.brave
  r["navigator.brave"]           = g(function(){ return typeof navigator.brave; });

  return JSON.stringify(r);
})()
"""


def _brave_major():
    app = r"C:\Program Files\BraveSoftware\Brave-Browser\Application"
    try:
        v = [int(re.match(r"^(\d+)\.", d).group(1)) for d in os.listdir(app)
             if re.match(r"^\d+\.\d+\.\d+\.\d+$", d)]
        return max(v) if v else 154
    except Exception:
        return 154


def uc_surucu(gorsel_blok, cdp_kullan):
    o = uc.ChromeOptions()
    o.add_argument("--disable-dev-shm-usage")
    o.page_load_strategy = "eager"
    d = uc.Chrome(options=o, browser_executable_path=BRAVE_PATH,
                  version_main=_brave_major(), no_sandbox=False)
    if cdp_kullan:
        # oto_tarama.py'nin yaptigi CDP setup'i birebir uygula — denetim
        # botun GERCEK halini olcsun.
        d.execute_cdp_cmd("Network.enable", {})
        d.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
        if gorsel_blok:
            d.execute_cdp_cmd("Network.setBlockedURLs",
                              {"urls": ["*.jpg", "*.png", "*.webp", "*.svg"]})
    return d


def attach_surucu(port):
    o = webdriver.ChromeOptions()
    o.debugger_address = f"127.0.0.1:{port}"
    return webdriver.Chrome(options=o)


def main():
    argv = sys.argv[1:]
    port = None
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    cdp_kullan = "--cdp-yok" not in argv
    gorsel_blok = "--gorsel-blok" in argv

    if port:
        print(f"MOD    : ELLE ACILMIS Brave'e baglaniyor (port {port})")
        d = attach_surucu(port)
    else:
        print(f"MOD    : uc ile yeni Brave (CDP setup={'var' if cdp_kullan else 'yok'}, "
              f"gorsel_blok={'var' if gorsel_blok else 'yok'})")
        d = uc_surucu(gorsel_blok, cdp_kullan)
    try:
        # SAHIBINDEN'E ISTEK YOK — bos sayfada olcuyoruz.
        # NOT: data: URL'e navigasyon Chrome tarafindan engelleniyor (script
        # bos context'te calisip None donuyordu) -> about:blank kullaniyoruz.
        # --https: userAgentData gibi API'ler SECURE CONTEXT ister; about:blank'te
        # yok gorunebilir. Alakasiz bir HTTPS sayfasinda olcum daha dogru
        # (sahibinden'e HIC istek atilmaz).
        d.get("https://example.com/" if "--https" in sys.argv else "about:blank")
        print(f"SAYFA  : {d.current_url}")
        ham = d.execute_script("return " + JS.strip())
        if not ham:
            raise SystemExit(f"[HATA] script sonuc dondurmedi (sayfa={d.current_url})")
        sonuc = json.loads(ham)
        print("-" * 72)
        for k, v in sonuc.items():
            print(f"{k:28s} : {v}")
        print("-" * 72)
        # Kaba bir hukum
        kirmizi = []
        if sonuc.get("navigator.webdriver") in (True, "true"):
            kirmizi.append("navigator.webdriver=true")
        if sonuc.get("cdc_ degiskenleri") not in ("yok", None):
            kirmizi.append("cdc_ degiskenleri sayfada")
        if sonuc.get("plugins.length") == 0:
            kirmizi.append("plugins bos")
        if "VAR" in str(sonuc.get("CDP izi (stack)")):
            kirmizi.append("CDP Runtime izi")
        if sonuc.get("toString yamasi") not in ("temiz", None):
            kirmizi.append("native imza yamali: " + str(sonuc.get("toString yamasi")))
        print("KIRMIZI BAYRAKLAR: " + (", ".join(kirmizi) if kirmizi else "yok"))
    finally:
        if not port:
            try:
                d.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
