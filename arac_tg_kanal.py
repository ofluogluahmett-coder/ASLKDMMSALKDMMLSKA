"""
TELEGRAM HEDEF BULUCU — botun yazacagi grubu/kanali bulur ve .env'e yazar.

NEDEN: besleme ilk kurulumda kullanicinin OZEL sohbetine yaziyordu
(chat_id pozitif, kisiye ait). Ozel sohbet PAYLASILAMAZ, bu yuzden ortak
ilanlari goremiyordu. Cozum: gercek bir grup/kanal acip botu icine almak.

KULLANIM
  1) Telegram'da yeni GRUP olustur
  2) Gruba @otobotpro_bot'u ekle
  3) Gruba herhangi bir mesaj yaz ("test" yeter)
  4) py -3.12 arac_tg_kanal.py          -> bulunan hedefleri listeler
     py -3.12 arac_tg_kanal.py --yaz    -> en uygun hedefi .env'e yazar

NOT: Telegram getUpdates yalnizca SON guncellemeleri verir (~24 saat).
Hicbir sey gorunmuyorsa gruba bir mesaj daha yaz ve tekrar calistir.

GRUP/KANAL LIMITI: gruplarda dakikada ~20 mesaj siniri var. Hedef gruba
cevrilince mesaj araligi (OTO_TG_ARA) 3.5 sn'ye cekilir — dakikada ~17,
sinirin altinda.
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent


def token_al():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    import os
    t = (os.getenv("TELEGRAM_TOKEN") or "").strip()
    if not t:
        print(".env icinde TELEGRAM_TOKEN yok.")
        sys.exit(1)
    return t


def getir(token, uc):
    url = f"https://api.telegram.org/bot{token}/{uc}"
    with urllib.request.urlopen(url, timeout=20) as c:
        return json.load(c)


def hedefleri_bul(token):
    d = getir(token, "getUpdates")
    if not d.get("ok"):
        print("getUpdates basarisiz:", str(d)[:200])
        return []
    bulunan = {}
    for g in d.get("result", []):
        for alan in ("message", "channel_post", "my_chat_member",
                     "edited_message"):
            o = g.get(alan)
            if not o:
                continue
            ch = o.get("chat") or {}
            if not ch.get("id"):
                continue
            bulunan[ch["id"]] = {
                "id": ch["id"],
                "tip": ch.get("type", "?"),
                "ad": ch.get("title") or ch.get("username")
                      or ch.get("first_name") or "",
            }
    return list(bulunan.values())


def env_yaz(chat_id):
    p = ROOT / ".env"
    s = p.read_text(encoding="utf-8-sig")
    yeni, n = re.subn(r"(?m)^TELEGRAM_CHAT_ID=.*$",
                      f"TELEGRAM_CHAT_ID={chat_id}", s, count=1)
    if n == 0:
        yeni = s.rstrip("\n") + f"\nTELEGRAM_CHAT_ID={chat_id}\n"
    # Grup limiti icin mesaj araligini genislet (dakikada ~20 sinir)
    yeni2, m = re.subn(r"(?m)^OTO_TG_ARA=.*$", "OTO_TG_ARA=3.5", yeni,
                       count=1)
    if m:
        yeni = yeni2
    p.write_text(yeni, encoding="utf-8")
    print(f".env guncellendi: TELEGRAM_CHAT_ID={chat_id}"
          + (" ve OTO_TG_ARA=3.5" if m else ""))
    print("Sunucunun yeniden baslatilmasi gerekiyor (kuyruk bosalinca).")


def main():
    token = token_al()
    me = getir(token, "getMe")
    print("bot:", "@" + me["result"]["username"])
    hedefler = hedefleri_bul(token)
    if not hedefler:
        print("\nHicbir hedef bulunamadi.")
        print("Gruba bir mesaj yaz ve tekrar calistir "
              "(getUpdates sadece son guncellemeleri verir).")
        return
    print("\nBULUNAN HEDEFLER")
    for h in hedefler:
        tip = {"private": "OZEL SOHBET (paylasilamaz)",
               "group": "GRUP", "supergroup": "GRUP (super)",
               "channel": "KANAL"}.get(h["tip"], h["tip"])
        print(f"  id={h['id']:<16} {tip:<28} {h['ad']}")

    gruplar = [h for h in hedefler
               if h["tip"] in ("group", "supergroup", "channel")]
    if "--yaz" in sys.argv:
        if not gruplar:
            print("\nGrup/kanal bulunamadi; .env'e DOKUNULMADI.")
            print("Botu gruba ekleyip gruba bir mesaj yaz.")
            return
        if len(gruplar) > 1:
            print(f"\n{len(gruplar)} grup/kanal var; en son goruleni "
                  f"seciliyor. Baskasini istersen id'yi elle yaz.")
        env_yaz(gruplar[-1]["id"])
    elif gruplar:
        print("\nYazmak icin: py -3.12 arac_tg_kanal.py --yaz")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
