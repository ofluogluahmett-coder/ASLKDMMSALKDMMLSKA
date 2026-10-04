# -*- coding: utf-8 -*-
"""PX GEC — ayri yazilim. Tek isi: botu takip et, PX penceresi gelince gec.

KULLANICININ NET TARIFI (KRITIK):
  - SOLDA kucuk INSAN kutusu var (insan silueti)
  - SAGDA buyuk "basili tut" kutusu var (o BUYUK kutu DOLUYOR)
  - Sira: ONCE soldaki kucuk insan kutusuna TEK TIK
          -> 3-5 saniye bekle (sagdaki buyuk kutu doluyor)
          -> SONRA sagdaki buyuk kutuya TIK
  -> PX gecilir.

Calisma:
  - Brave penceresini bulur (win32gui)
  - Minimize ise restore eder
  - CDP ile #px-captcha koordinatini okur (varsa PX var demektir)
  - SOLDAKI insan kutusuna tiklar -> 4sn bekler -> SAGDAKI buyuk kutuya tiklar
  - PX gecene kadar dongude kalir

Kullanim:
  python px_gec.py            # surekli izle
  python px_gec.py --tek      # tek sefer dene
"""
import sys
import json
import time
import ctypes
import traceback
import urllib.request

def _port_bul():
    """Portu sirayla ara: --port argumani > cdp_port.txt (bot yazar) > 59964 (eski sabit)."""
    import os
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
    try:
        yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cdp_port.txt")
        with open(yol, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 59964


PORT = _port_bul()
BASE = "http://127.0.0.1:%d" % PORT

# --- ayarlar (kullanici tarifine gore) ---
# #px-captcha div'i 530x95. Iki parcali:
#   SOL  ~0-60px   : kucuk INSAN kutusu (insan silueti)
#   SAG  ~470-530px: buyuk "basili tut" kutusu (DOLAN buyuk kutu)
INSAN_OFFSET_X = 30     # insan kutusu (SOL): kutunun SOLUNDAN kac px iceri
BASILI_OFFSET_X = 60    # basili tut (SAG): kutunun SAGINDAN kac px iceri
INSAN_BEKLEME = 4.0     # insan kutusuna tikladiktan sonra bekleme (sn)
TIK_BEKLEME = 0.15      # tik arasi
DONGU_ARASI = 2.0       # PX yoksa bekleme (sn)


def log(*a):
    print("[px_gec]", *a)
    sys.stdout.flush()


# ============================================================ WIN32
user32 = ctypes.windll.user32


def pencere_bul():
    """Brave penceresini bul. (hwnd, baslik, rect, minimize)"""
    import win32gui
    adaylar = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        t = win32gui.GetWindowText(hwnd)
        if t and "brave" in t.lower():
            adaylar.append((hwnd, t, win32gui.GetWindowRect(hwnd), win32gui.IsIconic(hwnd)))

    win32gui.EnumWindows(cb, None)
    if not adaylar:
        return None
    adaylar.sort(key=lambda a: (a[2][2]-a[2][0])*(a[2][3]-a[2][1]), reverse=True)
    return adaylar[0]


def pencere_hazirla(hwnd, minimize):
    """Minimize ise restore et, one getir."""
    import win32gui
    import win32con
    if minimize:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(1.0)
    try:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception:
        pass
    cl = win32gui.GetClientRect(hwnd)
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    return pt[0], pt[1], cl[2]-cl[0], cl[3]-cl[1]


def tikla(x, y):
    """OS-level gercek fare tiklamasi (SendInput)."""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    # sol tus bas
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
    log("  tiklandi: (%d, %d)" % (x, y))


def basili_tut(x, y, sure=1.2):
    """Basili tut: sol tusu bas, bekle, birak."""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(sure)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
    log("  basili tutuldu: (%d, %d) %.1fsn" % (x, y, sure))


# ============================================================ CDP
def cdp_baglan():
    """page target'a baglan, (ws, call, ev) dondur."""
    import websocket
    with urllib.request.urlopen(BASE + "/json/list", timeout=8) as r:
        targets = json.loads(r.read().decode("utf-8"))
    page = None
    for t in targets:
        if t.get("type") == "page":
            page = t
            break
    if not page:
        return None, None, None
    ws = websocket.create_connection(
        page["webSocketDebuggerUrl"], timeout=8, suppress_origin=True)
    _mid = [0]

    def call(method, params=None):
        _mid[0] += 1
        mid = _mid[0]
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    return None
                return m.get("result")

    def ev(expr):
        r = call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if not r:
            return None
        return r.get("result", {}).get("value")

    return ws, call, ev


PX_KONTROL_JS = r"""
(function(){
  try {
    var c = document.querySelector('#px-captcha');
    if (!c) return JSON.stringify({px:false});
    var r = c.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return JSON.stringify({px:false});
    var cs = getComputedStyle(c);
    if (cs.display === 'none' || cs.visibility === 'hidden') return JSON.stringify({px:false});
    return JSON.stringify({px:true, x:Math.round(r.left), y:Math.round(r.top),
                           w:Math.round(r.width), h:Math.round(r.height)});
  } catch(e){ return JSON.stringify({px:false, hata:String(e)}); }
})()
"""


def px_var_mi(ev):
    """PX challenge ekranda mi? Koord dondur veya None."""
    try:
        res = ev(PX_KONTROL_JS)
        d = json.loads(res)
        if d.get("px"):
            return d
    except Exception:
        pass
    return None


# ============================================================ ANA
def px_gec_bir_kere(ws, ev, client_x, client_y):
    """PX'i bir kez gecmeyi dene. True = basarili."""
    d = px_var_mi(ev)
    if not d:
        return False

    cap_x = client_x + d["x"]
    cap_y = client_y + d["y"]
    cap_w = d["w"]
    cap_h = d["h"]

    # SOLDAKI kucuk INSAN kutusu
    insan_x = cap_x + INSAN_OFFSET_X
    insan_y = cap_y + cap_h // 2
    # SAGDAKI buyuk "basili tut" kutusu (DOLAN buyuk kutu)
    basili_x = cap_x + cap_w - BASILI_OFFSET_X
    basili_y = cap_y + cap_h // 2

    log("PX BULUNDU! kutu ekran=(%d,%d) %dx%d" % (cap_x, cap_y, cap_w, cap_h))
    log("  INSAN kutusu (SOL) -> (%d,%d)" % (insan_x, insan_y))
    log("  BASILI kutu (SAG)  -> (%d,%d)" % (basili_x, basili_y))

    # 1) SOLDAKI kucuk insan kutusuna TEK TIK
    tikla(insan_x, insan_y)
    log("  insan kutusuna tiklandi, %0.1f sn bekleniyor (buyuk kutu doluyor)..." % INSAN_BEKLEME)
    time.sleep(INSAN_BEKLEME)

    # 2) SAGDAKI buyuk kutuya TIK
    tikla(basili_x, basili_y)
    time.sleep(1.5)

    # 3) gecti mi?
    if not px_var_mi(ev):
        log("  >>> PX GECILDI!")
        return True

    # 4) gecmediyse 2. tur
    log("  ilk tur yetmedi, 2. tur deneniyor...")
    tikla(insan_x, insan_y)
    time.sleep(INSAN_BEKLEME)
    tikla(basili_x, basili_y)
    time.sleep(2.0)
    if not px_var_mi(ev):
        log("  >>> PX GECILDI (2. tur)!")
        return True

    log("  >>> PX HALA VAR")
    return False


def main():
    tek = "--tek" in sys.argv
    log("### PX GEC basladi (tek=%s) ###" % tek)

    ws = None
    call = None
    ev = None
    son_baglanma = 0

    while True:
        try:
            # pencere
            p = pencere_bul()
            if not p:
                log("Brave penceresi yok, bekleniyor...")
                time.sleep(3)
                continue
            hwnd, baslik, rect, minimize = p
            client_x, client_y, cw, ch = pencere_hazirla(hwnd, minimize)

            # CDP baglanti (30 sn'de bir tazele)
            if ws is None or (time.time() - son_baglanma) > 30:
                try:
                    if ws:
                        ws.close()
                except Exception:
                    pass
                ws, call, ev = cdp_baglan()
                son_baglanma = time.time()
                if ws is None:
                    log("CDP baglanamadi, bekleniyor...")
                    time.sleep(3)
                    continue

            # PX var mi?
            d = px_var_mi(ev)
            if d:
                ok = px_gec_bir_kere(ws, ev, client_x, client_y)
                if tek:
                    log("tek mod: cikiliyor")
                    return
                time.sleep(2)
            else:
                # sessiz bekleme
                time.sleep(DONGU_ARASI)

        except KeyboardInterrupt:
            log("kullanici durdurdu")
            return
        except Exception as e:
            log("dongu HATA:", e)
            try:
                if ws:
                    ws.close()
            except Exception:
                pass
            ws = None
            time.sleep(3)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("GENEL HATA:")
        log(traceback.format_exc())
