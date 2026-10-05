# OTO KELEPIR

`sahibinden.com/otomobil` tarayicisi — ikinci el otomobil ilanlarini toplar,
piyasa altinda fiyatlanmis olanlari (kelepir) tespit etmeyi hedefler.

Tarayici otomasyonu: **undetected_chromedriver + Brave**. Veri: **SQLite**.

---

## Iki bot var, karistirma

| Bot | Dosya | Ne yapar |
|---|---|---|
| **Sade toplayici** | `oto_tarama.py` | Sadece ilan listesini cekip DB'ye yazar. Skorlama/bildirim YOK. Anonim profil + cache-bust kullanir; PX/CF yemeden calisan sade cekirdek. Yeni gelistirmeye buradan baslamak kolay. |
| **Tam bot (Faz 1-2)** | `oto_bot.py` | Kalici login profili, detay sayfasi, KM normalizasyonu, bucket/skorlama, kohort takibi. Buyuk ve olgun; `kelepir.py` + `takip.py` ile birlikte calisir. |

Ikisi **ayri veritabani** kullanir: `oto_tarama.db` ve `oto_hafiza.db`.

---

## Kurulum

Gereken: **Python 3.12**, **Brave Browser**
(`C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe`), Windows.

```powershell
cd oto_kelepir
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

`.env` git'e **girmez** — her gelistirici kendi kopyasini tutar.
Faz 1 icin `.env`'i bos birakmak yeterli (`BRAVE_VERSION_MAIN` bos → otomatik
surum tespiti).

---

## Calistirma

### Sade toplayici

```powershell
baslat_tarama.bat
```

veya elle:

```powershell
$env:MAX_TUR=10            # 0 = sinirsiz
py -3.12 -u oto_tarama.py
```

- Tur periyodu sabit **~50-75 sn** (tarama + mola toplami — mola tarama
  suresinin USTUNE eklenmez).
- Her turda URL'e cache-bust parametresi eklenir (`&_=<ms>`) → origin taze
  liste dondurmeye zorlanir. **Bu parametreyi kaldirmayin**: kaldirildiginda
  liste ~3-5 dk bayatliyor.
- Gorsel/font/tracker byte'lari CDP ile bloklanir; ilan verisi DOM'da kalir.
- Olculen: 10 tur, tur basina 21-22 ilan, sayfa yuklemesi ~0.8 sn, PX/CF yok.

### Tam bot

```powershell
baslat.bat
```

Ilk calistirmada Brave gorunur acilir; CF/PX cikarsa **elle gec** (tiklamadan
bekle — CF kendiliginden geciyor). Profil `brave_oto_profile_login/` icine
kaydedilir, sonraki aciliSlarda tekrar sormaz.

### PX/CF gecici (ayri pencere)

```powershell
py -3.12 px_gec.py        # surekli izle
py -3.12 px_gec.py --tek  # tek sefer dene
```

Bot acilista kendi CDP debug portunu `cdp_port.txt`'ye yazar; `px_gec.py` bu
dosyadan okuyup ayni tarayiciya baglanir. Port sabitlemek gerekmez
(`--port 1234` ile elle de verilebilir).

---

## Dosya duzeni

| Dosya | Gorev |
|---|---|
| `oto_tarama.py` | Sade toplayici (tarama + DB). |
| `oto_bot.py` | Tam bot: tarama, detay, bucket, skorlama entegrasyonu. |
| `kelepir.py` | Skorlama motoru (Faz 2). Tarayici kullanmaz, sadece DB okur. Tek basina da calisir: `py -3.12 kelepir.py` |
| `takip.py` | Kohort takibi — "kelepir" denen ilan gercekten satildi mi? Eslestirilmis kontrol grubuyla olcer. |
| `takip_runner.py` | Takibi botun disinda, bagimsiz process olarak calistirir (bot olse de kohort penceresi kaymasin). |
| `durum.py` | Tek kaynak durum raporu: veri sorularini DB'den, olay sorularini log'dan cevaplar. |
| `px_gec.py` | PerimeterX "basili tut" ekranini gecer (win32 fare + CDP). |
| `baslat.bat` / `baslat_tarama.bat` | Tek tikla baslaticilar. |
| `CLAUDE.md` | **Proje rehberi** — kelepir tanimi, KM normalizasyonu, bucket tasarimi, tum teshis gecmisi. Kod yazmadan once oku. |
| `docs/` | PX/CF saha notlari, elenen cozum adaylari. |

Uretilen (git'e girmez): `oto_hafiza.db`, `oto_tarama.db`, `oto_gorulmus.json`,
`*.log`, `brave_oto_profile*/`.

---

## Gelistirirken bilinmesi gerekenler

1. **Anonim profil > kalici profil.** Haftalarca bot kullanilan kalici profil
   `_px3`/`_pxvid` cerezleriyle damgalanip surekli PX yiyor. `oto_tarama.py`
   bu yuzden `user_data_dir` vermiyor.
2. **Cache-bust sart.** URL'e her istekte degisen bir parametre eklenmezse
   liste bayat geliyor.
3. **CF'ye TIKLAMA.** Turnstile kendiliginden geciyor; tiklamak bozuyor.
   PX ("basili tut") farkli — o gecilmeli (`px_gec.py`).
4. **`py -3.12 -u`** kullan. Cikti tamponlandigi icin log dosyasina
   yonlendirince "hic cikti yok" gorunur.
5. **Windows konsolu cp1254.** Kaynak kodda ASCII-safe yaz veya
   `PYTHONIOENCODING=utf-8` ile calistir.
6. `no_sandbox=False` **bilinctli**: uc, `no_sandbox=True` iken komuta
   `--test-type` ekliyor ve Brave 150+ bu flag'i gorunce aninda kapaniyor.

Detaylar: [CLAUDE.md](CLAUDE.md), [docs/px_kacinma.md](docs/px_kacinma.md)
(PX ile hic karsilasmama stratejisi + olculmus parametre listesi) ve
[docs/perimeterx_notu.md](docs/perimeterx_notu.md).

---

## Sonraki fazlar

- **Faz 2** — KM regresyonu + 4 katmanli bucket (kismen `kelepir.py`'de)
- **Faz 3** — Detay sayfasi: boya/degisen/hasar, kirmizi bayrak
- **Faz 4** — Kelepir tespiti + Telegram bildirim
