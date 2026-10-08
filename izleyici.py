"""
IZLEYICI — gun boyu kosuyu gozetler, SADECE mudahale gerektiren olaylari
basar. Her satir bir bildirim oldugu icin filtre dar tutulmustur.

NEDEN BOYLE: 08.10.2026'da 13 tur sessizce kayboldu ve kimse fark etmedi.
Sessizlik "her sey yolunda" demek DEGIL. Bu yuzden hem arizalari hem de
yarim saatlik NABIZ ozetini basar — hic satir gelmemesi de bir isarettir.

Basilan olaylar:
  ALARM / DUZELDI   veri akisi kesildi / geri geldi
  KAYIP / BOZUK     sayfa tarafi gonderimi basarisiz, bozuk istek
  TG HATA           Telegram gonderimi basarisiz
  BEKCI             sekme yenilendi / sekme yok
  NABIZ             yarim saatte bir ozet (sessizlik belirsiz kalmasin)

Kullanim: py -3.12 izleyici.py
"""
import json
import sys
import time
import urllib.request
from datetime import datetime

DURUM = "http://127.0.0.1:8765/durum"
ARALIK = 30            # sn — yerel okuma, ucuz
SESSIZ_ALARM = 360     # sn — tur araligi en fazla 150 sn; 6 dk kesinlikle ariza
NABIZ_ARASI = 1800     # sn — yarim saatlik ozet

# ── YENI ILAN KURAKLIGI ALARMI (08.10.2026 olcumu) ───────────────────
# Iki bagimsiz yontemle olculdu: bu kategoriye is saatlerinde dakikada
# ~5 yeni ilan giriyor (her 10-15 sn'de bir).
#   Yontem 1 (kaydettigimiz taze ilanlar, 10 dk kovalari): 1.7-8.3/dk
#   Yontem 2 (ID hizi): global dakikada ~208 ID, ardisik otomobil ID
#            farkinin medyani 41 -> 208/41 = ~5/dk
# Dolayisiyla 4 dakika boyunca HIC yeni ilan gelmemesi piyasa degil
# BORU HATTI arizasidir (o surede siteye ~20 ilan girmistir).
#
# 08.10.2026 KALIBRASYON: esik once 240 sn idi ve surekli caliyordu.
# Sebebi ariza degil, OLCULEN GERCEK: sahibinden bize dondurulmus bir
# liste veriyor ve bu goruntu ~5-6 dakikada bir tazeleniyor; yeni ilanlar
# 25-35'lik obekler halinde o anda geliyor (patlama anlari 10:34, 10:39,
# 10:45, 10:51, 10:56, 11:08, 11:30, 11:40). Yani 4-6 dakikalik kuraklik
# NORMAL. Esik o pencerenin USTUNE cekildi; boylece alarm sadece gercek
# arizada caliyor (iki pencere ust uste kacarsa).
KURAK_ALARM = 540      # sn (9 dk) — olculen onbellek penceresi ~5-6 dk
IS_SAATI = (8, 22)     # bu saatler arasinda kuraklik anlamlidir


def yaz(*a):
    print(f"{datetime.now():%H:%M:%S}", *a, flush=True)


def main():
    onceki = {}
    alarmda = False
    son_nabiz = 0.0
    sunucu_sessiz_bildirildi = False

    while True:
        try:
            with urllib.request.urlopen(DURUM, timeout=10) as c:
                d = json.load(c)
            sunucu_sessiz_bildirildi = False
        except Exception as e:
            if not sunucu_sessiz_bildirildi:
                yaz(f"ALARM  yerel sunucuya ulasilamiyor: {str(e)[:70]}")
                sunucu_sessiz_bildirildi = True
            time.sleep(ARALIK)
            continue

        # ── sunucu yeniden mi baslatildi? ──
        # Sayaclar sifirlandiginda izleyici bunu "kuraklik" sanip yanlis
        # alarm veriyordu (08.10.2026). Baslangic damgasi degistiyse
        # tum referanslar sifirlanir.
        bas = d.get("baslangic")
        if bas and onceki.get("baslangic") and bas != onceki["baslangic"]:
            yaz(f"SUNUCU YENIDEN BASLADI ({bas[11:]}) — referanslar "
                f"sifirlandi, bundan sonraki sayilar yeni oturumun")
            onceki.clear()
            alarmda = False
        onceki["baslangic"] = bas

        # ── challenge (PX dogrulama ekrani) ──
        # PX kendiliginden GECMIYOR; sebebi biliyorsak alarmda adiyla
        # soyle, "liste donmus olabilir" diye yanlis yone bakmayalim.
        sc = d.get("son_challenge")
        if sc and sc != onceki.get("son_challenge"):
            yaz(f"CHALLENGE  sayfa dogrulama ekraninda ({sc[11:]}) — "
                f"ELLE gecilmesi gerekiyor, PX kendi gecmiyor")
        onceki["son_challenge"] = sc

        # ── veri akisi kesildi mi? ──
        # Bekci freni acikken /durum, uzantidaki ESKI bekci kodunu
        # susturmak icin "son_gorulme"yi taze gosteriyor. Gercek deger
        # son_gorulme_gercek'te; olcum bozulmasin diye onu kullan.
        sg = d.get("son_gorulme_gercek") or d.get("son_gorulme")
        if d.get("bekci_frenlendi") and not onceki.get("fren_bildirildi"):
            onceki["fren_bildirildi"] = True
            yaz("BEKCI FRENI ACIK — sekme yenilemesi kapatildi "
                "(challenge/PX sirasinda yenileme ise yaramaz, blogu "
                "derinlestirir). Olcum son_gorulme_gercek'ten okunuyor.")
        gecen = None
        if sg:
            try:
                gecen = (datetime.now()
                         - datetime.fromisoformat(sg)).total_seconds()
            except Exception:
                gecen = None
        if gecen is not None:
            if gecen > SESSIZ_ALARM and not alarmda:
                alarmda = True
                bk = d.get("bekci") or {}
                yaz(f"ALARM  {int(gecen/60)} dakikadir veri yok "
                    f"(sekme={bk.get('sekme')}, donmus={bk.get('donmus')}, "
                    f"bekci={bk.get('eylem')})")
            elif gecen <= SESSIZ_ALARM and alarmda:
                alarmda = False
                yaz(f"DUZELDI  veri akisi geri geldi "
                    f"(tur={d.get('son_tur')})")

        # ── YENI ILAN KURAKLIGI ──
        # Olculen hiz dakikada ~5 ilan; 4 dakika kuraklik boru hatti
        # arizasi demektir (bayat liste, susturulmus besleme vb.).
        zy = d.get("zengin_yeni", 0)
        if zy > onceki.get("zengin_yeni", -1):
            onceki["son_yeni_an"] = time.time()
            if onceki.get("kurakta"):
                onceki["kurakta"] = False
                yaz(f"DUZELDI  yeni ilan akisi geri geldi "
                    f"(zengin={zy})")
        onceki["zengin_yeni"] = zy
        sa = datetime.now().hour
        if (IS_SAATI[0] <= sa < IS_SAATI[1]
                and onceki.get("son_yeni_an")
                and time.time() - onceki["son_yeni_an"] > KURAK_ALARM
                and not onceki.get("kurakta")):
            onceki["kurakta"] = True
            dk = (time.time() - onceki["son_yeni_an"]) / 60
            sebep = ("CHALLENGE ekraninda — elle gecilmeli"
                     if d.get("son_challenge")
                     else "liste donmus veya toplayici durmus olabilir")
            yaz(f"ALARM  {dk:.0f} dakikadir YENI ILAN yok "
                f"(beklenen: dakikada ~5). {sebep} — "
                f"tur={d.get('son_tur')}, sunucu_turu={d.get('istek')}")

        # ── sayac artislari (sadece artarsa bildir) ──
        for anahtar, etiket in (("kayip", "KAYIP  sayfa tarafi gonderimi "
                                           "basarisiz"),
                                ("bozuk", "BOZUK  gecersiz istek")):
            simdi_v = d.get(anahtar, 0)
            if simdi_v > onceki.get(anahtar, 0):
                yaz(f"{etiket}: +{simdi_v - onceki.get(anahtar, 0)} "
                    f"(toplam {simdi_v})")
            onceki[anahtar] = simdi_v

        tg = d.get("telegram") or {}
        if tg.get("hata", 0) > onceki.get("tg_hata", 0):
            yaz(f"TG HATA  Telegram gonderimi basarisiz "
                f"(toplam {tg['hata']})")
        onceki["tg_hata"] = tg.get("hata", 0)

        bk = d.get("bekci") or {}
        eylem = bk.get("eylem", "")
        if eylem and eylem != "izliyor" and eylem != onceki.get("eylem"):
            yaz(f"BEKCI  {eylem}")
        onceki["eylem"] = eylem

        # ── yarim saatlik nabiz (sessizlik belirsiz kalmasin) ──
        if time.time() - son_nabiz > NABIZ_ARASI:
            son_nabiz = time.time()
            yaz(f"NABIZ  tur={d.get('son_tur')} "
                f"sunucu_turu={d.get('istek')} "
                f"yeni={d.get('yeni')} zengin={d.get('zengin_yeni')} "
                f"tg={tg.get('gonderildi')} "
                f"doping_atlanan={tg.get('atlanan_doping')} "
                f"hafiza={d.get('hafiza_ilan')} "
                f"son_veri={int(gecen) if gecen is not None else '?'} sn once")

        time.sleep(ARALIK)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
