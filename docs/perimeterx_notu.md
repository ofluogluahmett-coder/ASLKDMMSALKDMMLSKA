# PerimeterX / Cloudflare — saha notlari

sahibinden.com'un vasita kategorisinde iki katmanli koruma var. Asagidakilerin
hepsi olculerek bulundu; tahmin yok.

> Bu dosyada **kimlik bilgisi / proxy sifresi YOK**. Proxy kullanacaksan
> `.env` icine `PROXY_LIST` olarak yaz (`.env` git'e girmez).

---

## 1. Iki katman

| Katman | Davranis |
|---|---|
| **Cloudflare Turnstile** | Kendiliginden ~5-46 sn'de geciyor. **TIKLAMAK BOZUYOR** — bekle, dokunma. |
| **PerimeterX** (`#px-captcha`) | "Access to this page has been denied" / "basili tut" ekrani. Elle veya `px_gec.py` ile gecilir. |

---

## 2. Kategori kurali

- **Vasita DISI kategoriler sorunsuz**: ikinci_el, cep_telefonu, bilgisayar,
  is_makineleri → 41-43 ilan geliyor.
- **Vasita kategorisi cok daha agresif**: otomobil, motosiklet, ticari, hasarli
  ve marka alt kategorileri PX'i kolay tetikliyor.

## 3. Profil damgasi — en onemli bulgu

Haftalarca bot kullanilan **kalici profil** `_px3` / `_pxvid` cerezleriyle
"bilinen bot" damgasi aliyor ve PX surekli geliyor.

**Cozum: anonim mod.** `user_data_dir` HIC verilmez → undetected_chromedriver
her acilista temiz gecici profil yaratir. `oto_tarama.py` boyle calisiyor ve
10 turluk testte PX/CF hic gelmedi.

Kanit (PC bilesenleri botunda ayni sorun): Brave **elle** acilinca ayni
bilgisayar + ayni IP'de sahibinden sorunsuz; **bot** acinca PX. Yani sorun IP
degil, otomasyon degil — damgali profildi.

## 4. URL tetikleyicisi (proxy + Camoufox denemelerinde olculdu)

Yanmis/kirli bir cikis IP'sinde query string ve path segmenti PX tetikliyordu:

| URL | Sonuc |
|---|---|
| `/otomobil` (duz, taze profil, taze IP) | 41 ilan |
| `/otomobil?page=2` | PX_BLOCK |
| `/otomobil/2` | PX_BLOCK |
| `/otomobil?sorting=date_desc` | PX_BLOCK |
| `/otomobil?a=1` (zararsiz parametre) | PX_BLOCK |

Temiz/anonim profilde `?sorting=date_desc` + cache-bust parametresi **sorunsuz
calisiyor** (bkz. `oto_tarama.py`). Yani bu tablo "yanmis oturum" halinin
belirtisi — sayfalama yapacaksan once oturumun temiz oldugundan emin ol.

## 5. Mobil site

`m.sahibinden.com` vasita kategorisi de PX hard block → cozum degil.

## 6. Elenenler

Ayri dosyada: [elenen_siklar.md](elenen_siklar.md) — Patchright, Camoufox,
mobil site, proxy havuzu; her biri icin kanit.

## 7. Henuz denenmemis fikirler

- SeleniumBase UC mode
- `curl_cffi` ile TLS/JA3 fingerprint degistirme (not: PX, JS calistirmayan
  client'lari 403 ile sert blokluyor — tek basina yetmedi)
- Bezier egrili insan benzeri fare hareketi

---

## 8. Gelistirirken dikkat

- Windows konsolu **cp1254**; Unicode box/emoji karakter `UnicodeEncodeError`
  verir. ASCII-safe yaz (`+--`, `|`, `[OK]`, `[PX_BLOCK]`) veya
  `PYTHONIOENCODING=utf-8` ile calistir.
- Python cikti **tamponlanir**; log dosyasina yonlendirirken `py -3.12 -u`
  kullan, yoksa "hic cikti yok" sanirsin.
