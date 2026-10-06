"""
KOPRU — temiz tarayicinin (oto_tarama.py) topladigi sayfayi, otobotun
KANITLANMIS ayristiricisindan gecirip zengin semaya (oto_hafiza.db) yazar.

NEDEN BOYLE
  oto_tarama.py PX'ten sag cikmayi cozdu ama ince veri tutuyor
  (baslik, fiyat, yil, km). kelepir.py ise zengin sema bekliyor
  (marka, seri, motor_hacim_grup, motor_tipi, paket, kimden, il...).
  Bu alanlarin HEPSI liste sayfasinda var ve otobot bunlari zaten
  cikariyor:
      td.searchResultsTagAttributeValue -> marka / seri / model
      td.searchResultsAttributeValue    -> yil / km
      td.searchResultsLocationValue     -> il / ilce
      a.store-icon                      -> galeriden / sahibinden
  Ayrica `parse_model_string()` model metninden motor hacmi/tipi/paketi,
  `arac_sinifi_hesapla()` sifir/ikinci-el ayrimini, `cop_tespit_baslik()`
  cop filtresini uyguluyor.

  Yani ayristiriciyi YENIDEN YAZMIYORUZ: otobotu ICE AKTARIP onun
  fonksiyonlarini kullaniyoruz. oto_bot.py'de `if __name__ == "__main__"`
  korumasi var, ice aktarmak botu CALISTIRMAZ.

  Yazma da otobotun `ilan_yaz()`'i ile: ekleme/guncelleme, fiyat dususu
  (ilk_fiyat, toplam_dusus_yuzde, dusus_sayisi), cop tespiti hepsi onun
  icinde. `kelepir.py` bucket modelini calisma aninda `ilan` tablosundan
  kurdugu icin ayri istatistik tablosu beslemeye gerek yok.

GUVENLIK
  Bu modul tarama akisini ASLA bozmaz: her hata yutulur ve (0, 0) doner.
  Gorsel INDIRILMIYOR — sadece HTML'deki URL metni saklanir (kullanici
  "ilan fotolari alinmasin" dedi; indirme yok, byte yok).

KULLANIM
  import oto_kopru
  eklendi, guncellendi = oto_kopru.html_isle(driver.page_source)
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
HAFIZA_DB = ROOT / "oto_hafiza.db"

_ob = None          # tembel yuklenen oto_bot modulu
_hata_yazildi = False


def _bot():
    """oto_bot'u tembel ice aktar (ilk cagrida)."""
    global _ob, _hata_yazildi
    if _ob is not None:
        return _ob
    try:
        import oto_bot
        _ob = oto_bot
    except Exception as e:
        if not _hata_yazildi:
            print(f"[KOPRU] oto_bot ice aktarilamadi, zengin yazma KAPALI: "
                  f"{str(e)[:120]}")
            _hata_yazildi = True
        _ob = False
    return _ob


def _baglan():
    con = sqlite3.connect(HAFIZA_DB, timeout=15.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=15000")
    return con


def html_isle(html):
    """Liste sayfasi HTML'ini zengin semaya yaz.

    Doner: (eklendi, guncellendi). Hata halinde (0, 0)."""
    ob = _bot()
    if not ob or not html:
        return 0, 0
    try:
        # DIKKAT: parse_ilanlar DEMET donduruyor -> (ilanlar, soup)
        _sonuc = ob.parse_ilanlar(html)
        ilanlar = _sonuc[0] if isinstance(_sonuc, tuple) else _sonuc
    except Exception as e:
        print(f"[KOPRU] parse hatasi: {str(e)[:120]}")
        return 0, 0
    if not ilanlar:
        return 0, 0

    eklendi = guncellendi = 0
    try:
        con = _baglan()
    except Exception as e:
        print(f"[KOPRU] DB acilamadi: {str(e)[:120]}")
        return 0, 0
    try:
        for ilan in ilanlar:
            try:
                vardi = con.execute("SELECT 1 FROM ilan WHERE ilan_id=?",
                                    (ilan["ilan_id"],)).fetchone()
                ob.ilan_yaz(con, ilan)
                if vardi:
                    guncellendi += 1
                else:
                    eklendi += 1
            except Exception:
                continue
        con.commit()
    except Exception as e:
        print(f"[KOPRU] yazma hatasi: {str(e)[:120]}")
    finally:
        try:
            con.close()
        except Exception:
            pass
    return eklendi, guncellendi


def durum():
    """Zengin DB'de kac ilan var, kaci skorlanabilir durumda?"""
    try:
        con = _baglan()
        n = con.execute("SELECT COUNT(*) FROM ilan").fetchone()[0]
        skorlanabilir = con.execute(
            "SELECT COUNT(*) FROM ilan WHERE durum='aktif' AND cop_mu=0 "
            "AND arac_sinifi='ikinci_el' AND fiyat>0 AND km IS NOT NULL "
            "AND yil IS NOT NULL").fetchone()[0]
        con.close()
        return n, skorlanabilir
    except Exception as e:
        return -1, -1


if __name__ == "__main__":
    n, s = durum()
    print(f"oto_hafiza.db -> toplam ilan: {n} | skorlanabilir: {s}")
