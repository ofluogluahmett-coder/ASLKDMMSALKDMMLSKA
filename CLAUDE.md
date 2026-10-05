# OTO KELEPİR AVCISI — Proje Rehberi

**Son güncelleme:** 05.10.2026 — PX kök nedeni bulundu: soğuk giriş.
**Ortaklar:** Ahmet (geliştirme + saha) · Adnan (saha + dağıtım ağı)

---

## 05.10.2026 (gece) — PX KÖK NEDENİ: SOĞUK GİRİŞ

**Teşhis zinciri (hepsi tek istekli ölçüm):**

| Saat | Ne | Sonuç |
|---|---|---|
| 19:11-19:27 | uc, anonim profil, doğrudan `/otomobil` | 11 tur, 252 ilan, **0 challenge** |
| ~19:40 | `&pagingSize=50` denemesi | **tek istekte hard block** |
| 22:43 | aynı IP, taze profil, doğrudan `/otomobil` | **PX_BLOCK** (3 saat sonra hâlâ) |
| 23:08 | aynı IP, `/masaustu-donanim` | **21 ilan** → damga tüm IP'yi kapsamıyor |
| ~23:30 | **kullanıcı ELLE** aynı IP'de `/otomobil` (sıralama + ilan detayı) | **hiç PX yok** → damga IP'de DEĞİL |
| 23:45 | parmak izi denetimi (HTTPS sayfada) | `webdriver=False`, `cdc_` yok, `plugins=5`, native imzalar yamasız, `userAgentData` normal → **bariz otomasyon izi YOK** |
| 23:50 | tek değişiklik: oturum **ana sayfadan** başladı (29 çerez), sonra kategori | **21 ilan, 0.87s, PX YOK** |

**Kök neden:** sorun ne IP ne parmak izi — **giriş biçimi**. Sıfır geçmişli
yepyeni bir tarayıcının ilk hareketi olarak tarihe-göre-sıralı derin kategori
sayfasına dalmak (üstüne hiç görsel indirmemek) insan trafiğinde görülmeyen
bir desen. Sabahki 11 turun temiz geçmesi de bunu doğruluyor: **ilk** tur
çerezi kapmış, kalan 10 tur aynı oturumu kullanmış. Oturum tazeleme ve
challenge sonrası her yeni oturum aynı soğuk girişi tekrarlayınca blok
kalıcılaştı.

**Uygulanan — `isin()` (oto_tarama.py):** her YENİ oturumda (açılış, oturum
tazeleme, challenge sonrası) önce ana sayfa açılır, 4-9 sn beklenir, 1-3
hafif scroll yapılır, çerezler doğal yolla alınır; sonra kategoriye geçilir.

**Yeni env anahtarları:** `ISINMA` (1), `CACHE_BUST` (1), `CACHE_DISABLED`
(1; attach'ta 0), `GORSEL_BLOK` (1; attach'ta 0), `CDP_PORT` (boş).

**Yeni aletler:**
- `arac_px_olcum.py` — bir parametre güvenli mi? TEK istekle ölçer, temiz
  anonim profil, 20 dk bekleme kuralı alete gömülü, hükmü `px_olcum.log`'a yazar.
- `arac_parmak_izi.py` — sıfır riskli otomasyon izi denetimi (sahibinden'e
  istek atmaz). `--https` şart: `about:blank`'te `userAgentData` yok görünür.
- `baslat_brave_debug.bat` — ayrı profille (`brave_insan_profile`) debug
  portlu Brave; kullanıcı 1-2 dk normal gezer, bot `CDP_PORT=9222` ile O
  tarayıcıya bağlanır. Isınma yetmezse yedek plan.

**oto_bot.py'de yapılanlar:** `_sayfa_url(0)` artık düz URL döndürüyor
(`pagingOffset=0` eklenmiyor — botun ilk sayfayı bile parametreyle istediği
bulundu). `TEK_SAYFA=1` (plan `[(0,1)]`) ve `ANONIM_MOD=1` (`--user-data-dir`
hiç verilmez) env'leri eklendi, `.env`'e yazıldı. **Not:** otobotta ısınma
HENÜZ YOK — oto_tarama'daki `isin()` oraya da taşınmalı, kök neden orada da
geçerli.

**Ayrıştırılmadı:** 23:50 denemesinde ısınma + cache-bust kapalı + görsel açık
+ cache açık aynı anda değişti. Sıradaki ölçüm: ısınma AÇIK + cache-bust AÇIK
(gecikme sorunu için cache-bust gerekli) → yine temiz mi?

**Ölçülen parametre hükümleri:** `sorting=date_desc` TEMİZ · `&_=<ms>` TEMİZ
· `pagingSize` **YANIK (tek istekte)** · `pagingOffset>0` ölçülemedi.

---

## 04.10.2026 — Sade toplayıcı + repo temizliği

- **`oto_tarama.py` eklendi (yeni, sade toplayıcı).** PC bileşenleri botunun
  (`apex_predator/sahibinden_bot.py`) kanıtlanmış tarama çekirdeğinin otomobil
  sürümü. SADECE tarar + `oto_tarama.db`'ye yazar — kelepir/skorlama/Telegram
  YOK, ilan fotosu da ÇEKİLMİYOR. `oto_bot.py`'ye dokunulmadı, ayrı DB kullanır.
  - **Anonim mod** (`user_data_dir` verilmiyor) + cache-bust (`&_=<ms>`) +
    `Network.setCacheDisabled` + resource-blocking + `page_load_strategy=eager`.
  - **Sabit tur periyodu:** `bekleme = max(3, hedef - geçen)`, hedef
    `uniform(50,75)`s — mola tarama süresinin ÜSTÜNE eklenmiyor.
  - `uc.Chrome.__del__ = lambda self: None` — uc'nin kapanışta bastığı
    `WinError 6` traceback'i susturuldu (iş bittikten sonra, crash değil).
  - **ÖLÇÜM (10 tur):** tur başına 21-22 ilan, yükleme ~0.8s, 252 ilan DB'ye
    yazıldı, **PX/CF hiç gelmedi.** Temizlik sonrası 1 tur tekrar doğrulandı.
- **`px_gec.py` artık portu kendi buluyor.** Sabit `PORT = 59964` kaldırıldı →
  `--port` argümanı > `cdp_port.txt` (bot yazıyor) > 59964 sırası. Bot açılışta
  uc'nin seçtiği gerçek debug portunu `cdp_port.txt`'ye yazıyor.
  - **Eksik bağımlılık bulundu:** `pywin32` + `websocket-client` Python 3.12'de
    kurulu DEĞİLDİ — `px_gec.py` sessizce döngüde hata basıyordu. Kuruldu ve
    `requirements.txt`'e eklendi.
- **Klasör git'e hazırlandı (ortak geliştirme için).** 141 gereksiz öğe
  (`_*.py` deneme dosyaları, `oto_bot_run*.log`, eski profiller, camoufox
  klasörleri, dump'lar, ekran görüntüleri, `.env.ornek`/`.env.yedek_*`)
  **silinmedi**, `_arsiv/` altına taşındı ve gitignore'landı.
  - Çalışma durumu yerinde kaldı: `oto_hafiza.db*`, `oto_gorulmus.json`,
    `oto.log`, `brave_oto_profile_login/`, `brave_oto_profile_v2/`, `.env`.
  - `git init` + ilk commit = **17 dosya / 0.36 MB** (kod + docs). `.env`,
    veritabanı, profil, log repoya GİRMEDİ.
  - `README.md` geliştirici için yeniden yazıldı; `docs/` eklendi
    (`perimeterx_notu.md`, `elenen_siklar.md`, `px_gec_notu.md`).
    Proxy şifreleri/hesap adı docs'tan temizlendi (`<proxy_host>`).
  - `baslat_tarama.bat` eklendi (sade toplayıcı için tek tık, `MAX_TUR` env).

---

## Projenin Amacı

sahibinden.com otomobil ilanlarını sürekli tarayıp **piyasa altı fiyatlı
araçları** tespit eden bot sistemi.

**Kritik fark (donanım projesinden):** Burada "git bunu al" denmiyor.
Hedef şu: *"Bu araç olması gerekenden ucuz, git bak — ve ilk gören sen ol."*

Alım kararı, ekspertiz, tramer sorgusu **insanın işi.** Botun işi
çöplüğü eleyip bakmaya değer olanı öne çıkarmak.

### Başarı ölçüsü

**Hedef: %51 isabet oranı.**

```
Bot 100 ilan gösterdi
  51'i → "gidip bakmaya değerdi"  ✅
  49'u → "vakit kaybı"            ❌
```

Bu oran ölçülmeden geliştirme yapılamaz. Etiketleme mekanizması Faz 4'te
Telegram inline butonlarıyla kurulacak (bot şu an Faz 1 — sadece veri).

---

## Temel İçgörü — otoda kelepir nasıl tanımlanır

Donanımda mantık basitti: `RTX 3060 ort 10.900₺ → 8.500₺ = kelepir`

Otoda bu **doğrudan çöker.** Aynı model/yıl iki araç %40 fark edebilir
ve ikisi de doğru fiyattır — biri 1.4 TSI, diğeri 1.6 TDI, biri sıfır
km biri 300k km, biri galericiden biri sahibinden.

Ama liste sayfasındaki alanlar (**yıl, km, marka+seri+motor+paket +
sıfır/ikinci el + kimden**) fiyatın **%70-75'ini** açıklıyor. Geri
kalanı hasar/boya/donanım.

```
Beklenen fiyat = f(yıl, km, model/seri, motor, paket, sınıf, kimden)
Gerçek fiyat   = beklenen ± hasar/boya/bakım

Sapma %10-25 → muhtemelen hasar/boya (normal)
Sapma %35+   → hasar bunu açıklayamaz → GERÇEK SİNYAL
```

**Kilit nokta:** Ağır hasar fiyatı ~%25-35, boya ~%10-15 düşürür.
Yani %40+ sapma hasarla açıklanamaz. Hasarı bilmemize gerek yok —
**hasarın açıklayamayacağı kadar ucuz olanı** arıyoruz.

---

## KM Normalizasyonu — sistemin kalbi (Faz 2)

Bu olmadan hiçbir şey çalışmaz.

```
2018 Passat 1.6 TDI
   90.000 km → 1.280.000₺
  180.000 km → 1.050.000₺
  320.000 km →   820.000₺
```

Ham ortalama ~1.050.000₺ çıkar; 90.000 km'lik araç asla "kelepir"
görünmez — oysa gerçek fırsat odur.

**Yöntem:** İlan fiyatını referans km'ye normalize et.

```python
beklenen_km = (BUGUNKU_YIL - model_yili) * 18000
km_farki    = gercek_km - beklenen_km
duzeltme    = km_farki * km_katsayisi   # bucket'tan öğrenilir
```

`km_katsayisi` her bucket için **veriden regresyonla** öğrenilir.
Yeterli veri yoksa segment varsayılanı kullanılır (her 10.000 km ≈
fiyatın %1.5-2'si).

---

## Motor / Paket Ayrıştırma — otonun kritik boyutu

Aynı marka+seri+yıl bucket'ı çöker çünkü Passat 2018:
- 1.4 TSI Comfortline: 1.04M
- 1.6 TDI Comfortline: 1.64M
- 1.6 TDI Impression: 1.58M
- 1.6 TDI Highline:  1.92M

Bu yüzden `model` string'i (`"1.6 TDI BlueMotion Comfortline"`)
3 kolona ayrıştırılır:

```python
motor_hacim  REAL   → 1.6
motor_tipi   TEXT   → tdi
paket        TEXT   → comfortline
```

**Ayrıştırma kuralları:**
- Hacim: `\d\.\d{1,2}` regex (1.6, 2.0, 1.33)
- Audi ETS: `(20|25|30|35|40|45|50|55) (TDI|TFSI|TSI)` → hacim yerine ETS kodu (35→35.0)
- Motor tipi: sözlük — tdi, tsi, dci, multijet, cdi, hdi, crdi, tce, ecoboost, bluehdi, tfsi, fsi, jtd, jtdm, ts, fire, puretech, multiair, e-torq, cdti, ecotec, vti, thp, gdi, mpi, kappa, i-vtec, vtec, d-4d, vvti, mivec
- Paket: sözlük — comfortline, highline, trendline, elegance, titanium, dynamic, icon, style, sport, premium, executive, ambiente, zetec, easy, urban, cross, touch, intens, joy, active, allure, feel, distinctive, ambition, attraction, impression, inspiration, exclusive, lounge, business, cosmo, enjoy, essentia, comfort, elite, prime, acenta, tekna, visia, avantgarde, amg, life, expression, extreme, gt, gts, gti, gtd, gli, sline, rline, msport, stline, st, pop, hybrid, trend, connect, montecarlo, delight, pulse, sensation, midline
- Gürültü kelimeleri (sözlük DIŞI olduğu için otomatik atlanır):
  bmt, bluemotion, dsg, edc, tiptronic, s-tronic, multitronic, otomatik,
  manuel, 4x4, quattro, xdrive

**Default değerler (v4 — kritik iyileşme):**
Parser bulamazsa boş bırakmak yerine:
- `motor_tipi` boşsa → `"nasp"` (naturally aspirated, atmosferik)
- `paket` boşsa → `"standart"`

Böylece L1 bucket her ilana uygulanabilir hale gelir. Ölçüm gösterdi ki
motor_tipi='' olanların %100'ünde gerçekten turbo/injection kodu yok —
bunlar atmosferik motorlar. Default doğal davranışı yansıtır.

**Mevcut başarı oranı** (~7800 ilanla, v4 sonrası):
- motor_hacim: %83
- motor_tipi: %100 (nasp default ile)
- paket: %100 (standart default ile)
- **Üçü de dolu (L1 uygun): %83** ← %36'dan %83'e sıçradı

Hâlâ ayrıştırılamayanlar: sadece hacim tespit edilemeyen model
string'leri (`420d M Sport`, `316i Sport Line`, `S`, `A110`). Bunlar
motor_hacim NULL kalır → L1'e giremez, L3'te yakalanır.

---

## Sıfır km / İkinci El Ayrımı

Yeni araç ile 300k km 15 yıllık araç aynı bucket'ta olamaz.

```python
arac_sinifi = 'sifir'      if km <  1000 else 'ikinci_el'
```

Clio 2025 örneği (temizlenmeden önce spread=%206):
- galeriden + ikinci_el: n=26, ort=1.34M, **spread=%17** ✓

Bucket key'inde `arac_sinifi` ayrık boyut — sıfır km ilanlar kendi
kategorisinde.

---

## Kimden Ayrımı — bucket İÇİNDE 3 agrega

Galerici marj koyduğu için ortalama fiyatlar bireysel satıştan daha
yüksek. Ama aynı arac **bucket_key'de bölünmüyor** — aynı araç
galerici/bireysel diye ikiye bölünmesin. Bunun yerine tek bucket
satırında **3 ayrı agrega**:

- `ort_fiyat_bireysel`, `medyan_bireysel`, `n_bireysel` — kimden='sahibinden'
- `ort_fiyat_galerici`, `medyan_galerici`, `n_galerici` — kimden='galeriden'
- `ort_fiyat`, `medyan_fiyat`, `ilan_sayisi` — tümü

**Bireysel ort = taban (alış), galerici ort = tavan (satış).** Aradaki
fark = **çıkış marjı**. Örnek: VW Passat 1.6 TDI Comfortline 2018:
bireysel 1.53M, galerici 1.74M → marj %13.3 (Adnan için değerli sinyal).

**Uç değer koruması ÜÇÜNE DE ayrı uygulanır** — bireysel outlier'ı
galerici agregasını kirletemez, tersi de.

Galerici ilanları toplanmaya devam eder (piyasa ölçüsü için gerekli).

---

## Bucket Tasarımı — kademeli 3 katman

Fazla dar → veri kalmaz. Fazla geniş → ortalama anlamsızlaşır.
Çözüm: kademeli düşüş.

| Katman | Anahtar | Not |
|---|---|---|
| **L1** | marka+seri+motor_hacim+motor_tipi+paket+yıl+arac_sinifi | En doğru, en az veri |
| **L2** | marka+seri+motor_hacim+motor_tipi+yıl+arac_sinifi | Paket farkını yutar |
| **L3** | marka+seri+yıl+arac_sinifi | Son çare (motor bilinmez) |

`kimden` bucket key'de değil — her satırda 3 agrega ile ayrık tutulur
(yukarıdaki "Kimden Ayrımı" bölümüne bak).

Kelepir avcısı (Faz 4) karar verirken: **L1 ≥N ise L1, yoksa L2, yoksa L3.**
Kullanılan katman güven skorunu doğrudan etkiler.

**Uç değer koruması** (bucket_stat'a yazarken):
- Kaba ortalama hesapla
- Fiyat, kaba_ort × 0.3 - kaba_ort × 3 dışındaysa **bucket'a katkı verme**
- `fiyat_gecmisi`'ne INSERT olur (ham veri korunur), sadece agrega kirlenmez

---

## Kademeli Sinyal (Faz 4)

Tek eşik yok. "Git al" değil, "bak" diyoruz.

| Seviye | Koşul | Anlam |
|---|---|---|
| 🔥 **VURGUN** | sapma ≥ %35 + hasar temiz + güven ≥ 70 | Hemen ara |
| ⚡ **FIRSAT** | sapma %25-35 + güven ≥ 60 | Bugün bak |
| 👀 **İZLE** | sapma %18-25 veya güven düşük | Vakit olunca |
| 📋 **KAYIT** | sapma < %18 | Sadece veri, bildirilmez |

---

## Coğrafya — sınır yok, PUANLAMA var

Tüm Türkiye taranır. Mesafe güven skorunu etkiler:

```
İstanbul / Kocaeli / Tekirdağ  →   0
Marmara geneli                 →  -5
Ankara / İzmir / Bursa         → -10
Uzak iller                     → -20
```

Uzak ildeki küçük sapma yormaz, büyük sapma yine görünür
(dağıtım ağına paslanabilir).

---

## Detay Analizi (Faz 3) — sadece adaylar için

Liste sayfası yanıltır. Sapma ne kadar büyükse hasar ihtimali o kadar
yüksek — yani botun en heyecanlı bulduğu ilanlar en riskli olanlar.

**Akış:**

```
Sapma ≥ %18 → detay kuyruğuna
   ↓ mola içinde, worker thread
fetch → detay HTML  (~0.8 sn, donanımdaki yöntem)
   ↓
KIRMIZI BAYRAK (anında ele):
  ağır hasar kayıtlı = Evet
  değişen parça ≥ 4
  yabancı plaka / gümrüksüz
  "senetli" / "vadeli" / "kefil"
  "hasarlı" / "pert" / "kaza"
  "motor arızalı" / "şanzıman"
   ↓ temizse
HASAR PUANI:
  boyalı       × 1.0
  lokal boyalı × 0.5
  değişen      × 3.0

  0-2   → TEMİZ    ✅
  3-6   → ORTA     ⚠️
  7-12  → HASARLI  🟠
  13+   → AĞIR     ❌ ele
   ↓
SAPMAYI DÜZELT: hasar puanı başına ~%2 indirim normaldir
  → "fazladan sapma" hesaplanır → gerçek sinyal budur
```

Faz 3 ayrıca: yakıt/vites/renk/kasa detay sayfasından çekilecek
(liste sayfasında YOK).

---

## CF/Block Geri Çekilme Mantığı (17.09.2026)

Bot CF ekranı görünce ısrarla istek atıp **sert blok yiyordu** — 5 sayfa
denerken her biri yeni bir CF tetikliyordu. Çözüm: tek durum sınıflandırıcı
+ duruma göre farklı geri çekilme stratejisi.

### `_sayfa_durumu(html, title)` → 'ok' | 'cf' | 'block' | 'bos'

```
CF     : title'da "just a moment"/"attention"/"bir saniye"/"cloudflare" vb.
block  : HTML < 50KB (soft-block, gercek liste sayfasi 300-500KB olur)
bos    : HTML yeterli boyutta ama searchResultsItem yok
ok     : hicbiri
```

**Kritik ders — yanlış pozitif tuzağı:** İlk taslakta body-content'te
`"turnstile"` string'i de aranıyordu. Test sırasında gerçek bir liste
sayfasında `id="turnStileArea"` (gizli, `display:none`, login popup
widget'ı) bulundu — her normal sayfa yanlışlıkla "cf" sayılıyordu. Bu,
`static.cloudflareinsights.com` dersinin (CF detection ilk versiyonu)
tekrarı. **Ders: body-content string arama CF/block tespitinde güvenilmez
— sadece title + HTML boyutu + DOM element varlığı güvenilir sinyal.**
Değişiklik sonrası `_sayfa_durumu()` SADECE bunlara bakıyor, doğrulandı
(5 test: 4 sentetik + gerçek faz0_dump).

### Durum bazlı davranış

| Durum | Aksiyon |
|---|---|
| `ok` | Sayaç sıfırla, normal devam |
| `cf` | **Yeni istek atmayı durdur.** Pencere restore edilir (minimize'dan çıkar). 60sn bekle → title kontrol. Hâlâ CF ise 5dk bekle → tekrar (3 kez). 3 kez başarısız → 30dk mola. |
| `block` | Kademeli mola: 1. kez 30dk, 2. kez 2 saat, 3.+ kez 6 saat + kritik log. Bu sürede hiç istek atılmaz. |
| `bos` | 1 tur atla, 10dk mola. |

**Kritik kural:** `cf`/`block`/`bos` durumunda **o turun kalan sayfaları
taranmaz** — döngüden hemen çıkılır (`tur_kesildi=True`, `break`), normal
tur-periyodu molası da atlanır (zaten backoff molası uygulandı, üstüne
eklenmez).

### Detay worker da susar

`cf_block_event` (threading.Event) — ana döngü CF/block/bos tespit
edince set eder, worker kuyruktan çekmeyi durdurur. Worker'ın kendi
block backoff'u da aynı event'i set eder — **iki cephe birbirini
durdurur**, aynı anda istek atıp durumu kötüleştirmezler.

### Genel ritim (17.09 sonrası)

```
TUR_PERIYOT      = 180-300 sn
SAYFA_ARASI      = 8-16 sn
RENDER_SETTLE    = 2.5-4.0 sn
```

### Kök neden analizi — neden PC bileşenleri botu block yemiyordu (17.09.2026)

Kullanıcı sordu: aynı sahibinden.com, aynı Selenium/undetected_chromedriver
altyapısı, neden PC bileşenleri botu (`apex_predator/sahibindennormal.py`)
hiç block yemiyor da oto botu yiyor? Referans kodla satır satır karşılaştırma:

| | PC botu (çalışan) | Oto botu (block yiyen — düzeltme öncesi) |
|---|---|---|
| Endpoint sayısı | 5 farklı kategori (masaustu-donanim, PS5, PS4, Xbox S/X) | 1 kategori (otomobil) |
| Sayfalama | HİÇ `pagingOffset` yok — her endpoint hep 1. sayfası | `pagingOffset=0,20,40,60,80` — aynı sorgunun 5 derin sayfası |
| İstek deseni | Ana link her turda, ikincil linkler 2-3 turda **bir** | Her turda **hepsi**, sırayla, deterministik artan |
| Linkler arası bekleme | 1.0-2.5sn (kısa, ama her biri farklı endpoint) | 8-22sn (uzun, ama hepsi aynı endpoint) |

**Kök neden hız değil, DESEN:** "Aynı sorgunun ardışık derin sayfalarını
saniyeler içinde sırayla gezme" (0→20→40→60→80, her turda aynı sıra)
klasik scraper imzasıdır — organik kullanıcı böyle davranmaz. PC botu bunu
hiç yapmıyor, farklı kategorilere dağılarak "her istek yeni bir sayfa"
görünümü veriyor.

(Not: PC botu `--no-sandbox` kullanıyor ve sorun yaşamıyor; oto botu
kullanmıyor ve yine de block yiyordu — yani bu bayrak kök neden değil,
CLAUDE.md'deki eski "no-sandbox CF yakalıyor" notu yanlış genellemeydi.)

**Çözüm — kademeli derinlik planı** (`SAYFA_DERINLIK_PLANI`), PC botunun
ANA_LINK/IKINCIL_LINK N-turda-bir mantığının offset'lere uyarlanması:

```python
SAYFA_DERINLIK_PLANI = [
    (0,  1),   # pagingOffset=0  → HER turda (ana sayfa gibi)
    (20, 2),   # pagingOffset=20 → her 2 turda bir
    (40, 3),   # pagingOffset=40 → her 3 turda bir
]
```

`_tur_offsetleri(tur)` bu turda taranacak offsetleri döndürür VE **sırasını
karıştırır** (`random.shuffle`) — deterministik artan pattern tamamen
kırılır. Sonuç: ortalama istek/tur 3'ten ~1.8'e düştü (%40 azalma) + desen
PC botununkine yaklaştı. `MAX_SAYFA` .env değişkeni geriye uyumluluk için
hâlâ okunuyor (log/varsayılan amaçlı) ama tarama artık sabit `range()`
değil, plana göre.

### Kalıcı profil kaldırıldı — CF "basılı tut" damgası (17.09.2026)

> ⚠️ **BU BÖLÜM ARTIK GEÇERSİZ.** Buradaki "kök neden bulundu" iddiası
> 19.09'da çürütüldü — aşağıdaki **"CF teşhisi: ölen teoriler"** bölümünü
> oku. Kalıcı profil 19.09'da geri getirildi. Bu bölüm tarihsel kayıt
> olarak duruyor; buradaki mantığı tekrar uygulama.

**Kök neden bulundu:** CF interaktif challenge'ın ("basılı tut") asıl
sebebi kalıcı profil (`--user-data-dir=brave_oto_profile`) idi. Bu profil
saatlerce agresif trafik taşıdı, CF muhtemelen o fingerprint'i/profili
"kötü itibarlı" olarak damgaladı — profil değişmediği sürece her seferinde
zorlu challenge çıkıyordu.

**Çözüm:** `_driver_olustur()`'dan `--user-data-dir` argümanı **tamamen
kaldırıldı**. `uc.Chrome()` artık her açılışta **geçici, anonim bir profil**
oluşturuyor (instance kapanınca silinir) — CF geçmişi/damgası taşınmıyor.
`no_sandbox=False` parametresi de eklendi (Brave 150+ için crash koruması).
Eski `brave_oto_profile/` klasörü (470MB) silindi.

**Doğrulama:** Profil kaldırıldıktan sonra **3 tur art arda tamamen
temiz** (block/CF yok, yeni_orani %98-100) — önceki saatlerdeki sık block
davranışıyla tezat. Cache-bust (`_cache_bust`) ve tur periyodu (180-300sn)
zaten nazikti, değiştirilmedi.

**Önemli sonuç — mimari değişti:** Pencere artık minimize edilip
saklanmıyor (ki zaten geçici profilde CF cookie'si kalıcı olmayacağı için
"bir kere geç, sonra sorma" stratejisi anlamsızlaşıyor). Her yeni driver
kurulumunda (R2 self-heal dahil) CF challenge'ı **yeniden çıkma ihtimali
var**, ama profil temiz olduğu için CF'nin JS-tabanlı (invisible)
kontrolünün yeterli gelmesi ve interaktif challenge'a hiç düşülmemesi
bekleniyor — ki ilk 3 tur bunu doğruladı.

### Chromedriver ağ engeli çözüldü (17.09.2026)

Bot bir noktada **hiç açılamaz** hale geldi — `uc.Chrome()` kurulumunda
sürekli `URLError [WinError 10054]` (bağlantı zorla kapatıldı). Teşhis:

1. `undetected_chromedriver`'ın `patcher.py` → `auto()` metodu, eğer
   `driver_executable_path` **açıkça verilmezse**, **her çağrıda** mevcut
   chromedriver'ı siler ve `googlechromelabs.github.io`'dan
   `fetch_release_number()` ile yeniden indirir — cache kavramı yok bu
   yolda.
2. Bu adres bu makineden **TLS/SNI seviyesinde engelli** — `curl.exe` ile
   doğrudan (Python/oto_bot.py'den bağımsız) test edilip doğrulandı: genel
   `google.com`/`github.com` çalışıyor, DNS doğru IP'yi veriyor (Cloudflare/
   Google/sistem DNS'i aynı sonucu döndürdü — DNS sorunu değil), ama bu
   spesifik GitHub Pages alt alanına TLS handshake sonrası bağlantı
   resetleniyor. Ağ/ortam kaynaklı, kod hatası değil.
3. **Çözüm:** `storage.googleapis.com` (chromedriver'ın asıl barındığı CDN)
   bu makineden **erişilebilir** — sadece `googlechromelabs.github.io`
   engelli. `raw.githubusercontent.com` üzerinden (farklı domain) gerçek
   sürüm JSON'u okunup indirme linki bulundu, chromedriver ZIP'i
   `storage.googleapis.com`'dan indirilip proje köküne (`chromedriver.exe`)
   yerleştirildi.
4. `_driver_olustur()` artık `driver_executable_path` parametresini
   **açıkça** veriyor (dosya varsa) — bu, `patcher.auto()`'nun
   `_custom_exe_path=True` dalına girmesini sağlıyor: sadece **lokal binary
   patch** yapılır, network'e hiç gidilmez.

**Önemli:** `chromedriver.exe` proje kökünde duran **manuel yerleştirilmiş**
bir dosya — `.gitignore`'da değil, silinmemeli. Brave güncellenip major
version değişirse (`brave://version`), bu dosyanın da güncellenmesi
gerekebilir — aynı yöntemle (`raw.githubusercontent.com` → JSON → doğru
versiyon → `storage.googleapis.com` indirme linki) yenilenebilir.

### Detay worker geçici kapalı (17.09.2026)

`fetch()`'ten `driver.get()` navigasyonuna çevirmek (yukarıdaki gibi
`Sec-Fetch-Dest` farkı hipotezi) **detay worker'ın block sorununu
çözmedi** — hâlâ sistematik olarak block yiyor. Aynı gece ayrıca 30dk'lık
bir block molası beklenmedik şekilde ~11 saat sürdü (muhtemelen makine
uykuya geçti, kod bug'ı değil — process CPU kullanımı sıfıra yakındı).
Bot kendi kendine toparlandı (R2 self-heal, driver yeniden kuruldu) ve
sonraki tur ana taramada **tam temiz** geçti — ama birkaç tur sonra yine
block yedi. **Sonuç: ana tarama kısmen iyileşti (bazı turlar temiz,
bazıları block), IP itibar sorunu tam çözülmedi.**

Karar: **`DETAY_WORKER_AKTIF = False`** — worker thread hiç başlamıyor,
`aday_uret()` çağrılmıyor. Kod silinmedi (mimari, `_detay_fetch_navigasyon`,
`driver_lock` senkronizasyonu hep yerinde duruyor), sadece devre dışı.
Önce ana taramanın uzun süreli (günler) stabilitesi kanıtlanacak, IP daha
fazla soğuyacak, sonra detay worker'a dönülecek — muhtemelen "yöntem"
(fetch vs navigasyon) meselesi değil, ham istek hacmi/IP itibarı meselesi.

### İlk açılış CF karşılama

`_cf_gec()` — botun ilk başlangıcında (driver yeni kurulduğunda) CF
çıkarsa: 10sn aralıklarla title izler (yeni istek atmaz), 120sn boyunca
sabırla bekler. Kullanıcı manuel "I am human" tıklayınca geçer.

---

## CF teşhisi: ölen teoriler (18-19.09.2026)

İki gün boyunca CF/block sorununun kök nedeni arandı. **Beş hipotez test
edildi, hepsi kanıtla çürütüldü.** Bu bölümün amacı aynı yolları tekrar
yürümeyi önlemek — aşağıdakileri TEKRAR DENEME, cevap orada değil.

| # | Hipotez | Nasıl çürütüldü |
|---|---|---|
| 1 | **Bot davranışı** (scroll/mouse yok, cache-bust var, ritim mekanik) | Hepsi düzeltildi (cache-bust kaldırıldı, scroll/mouse simülasyonu, ritim yavaşlatıldı) → block devam etti |
| 2 | **IP itibarı** (günün kümülatif trafiği IP'yi yaktı) | PC 12 saat kapalı kaldı, IP soğudu → ilk açılışta yine challenge. Ayrıca kullanıcı AYNI IP'den elle girince sorunsuz |
| 3 | **Otomasyon parmak izi** (`navigator.webdriver`, CDP sızıntısı) | Ölçüldü: `navigator.webdriver=False`, `plugins.length=7`, UA normal. uc'nin yaması Brave'de çalışıyor |
| 4 | **Boş/geçici profil** (her açılışta `cf_clearance` çöpe gidiyor) | Kalıcı profil eklendi + kullanıcı elle "ısıttı" (gerçek gezinme, 85MB→171MB) → yine 2. turda block |
| 5 | **Derin sayfalama** (pagingOffset=20/40 scraper imzası) | 2957 sayfa çekimi log'dan sayıldı: offset 0 → %2 hata, 20 → %3, 40 → %3. **Korelasyon yok** |
| 6 | **Geniş kategori** (`/otomobil` korumalı, dar sorgu kurtarır) | `endpoint_testi.py` ile ölçüldü: geniş %75, dar (marka + bireysel) %75. **Birebir aynı** |

### Endpoint testi sonucu (19.09, `endpoint_testi.py`)

6 endpoint (geniş `/otomobil` kontrol grubu + 4 marka + `/otomobil/sahibinden`),
PC botu ritmiyle (saatte ~200 istek), 12 tur, 72 istek:

```
tur  1: █████░ 5/6     tur  7: ░░░░░░ 0/6   ← hepsi birden
tur  2: ██████ 6/6     tur  8: █░░░░░ 1/6
tur  3: ██████ 6/6     tur  9: ██████ 6/6   ← hepsi birden düzeldi
tur  4: ██████ 6/6     tur 10: ███░░░ 3/6
tur  5: ██████ 6/6     tur 11: ████░░ 4/6
tur  6: █████░ 5/6     tur 12: ██████ 6/6
```

**Engel endpoint'e göre değil ZAMANA göre geliyor.** Bir tur içinde altı
endpoint birden çöküyor, sonraki turda altısı birden düzeliyor → bu bir
oturum/IP seviyesi anahtarı, URL/kategori meselesi değil. Dolayısıyla
marka bazlı bölme yapılmadı (fayda yok, karmaşıklık var).

**Not:** Bu, "otomobil dikeyi elektronik dikeyinden daha sıkı korunuyor"
ihtimalini çürütmez — sadece *dikey içinde* daraltmanın işe yaramadığını
gösterir. PC botu hâlâ başka bir dikeyde ve sorunsuz.

### Üç farklı duvar (19.09'da isimlendirildi)

Endpoint testinde üç ayrı savunma gözlendi; kod bunların ikisini
tanımıyordu (login duvarını 'bos' sanıyordu):

| Duvar | Title | Boyut | Kod durumu |
|---|---|---|---|
| CF challenge | `Bir dakika lütfen...` / `Just a moment...` | — | `cf` |
| Login duvarı | `sahibinden.com Giriş` | ~102KB | `giris` *(yeni)* |
| 2FA | `2 Aşamalı Doğrulama` | ~92KB | `2fa` *(yeni)* |
| Sert red | `Access to this page has been denied` | ~11KB | `block` |

`giris`/`2fa` durumunda bot **hiç istek atmaz** — pencereyi öne alır,
kullanıcı giriş yapana kadar açık sayfayı 15sn'de bir izler
(`_sayfa_duzeldi_mi`, navigasyon yok), çözülünce devam eder.

### ⚠️ Türkçe CF başlığı bug'ı (19.09 — önemli)

CF anahtar kelime listesi kodda **4 ayrı yere kopyalanmıştı** ve hiçbirinde
Cloudflare'in Türkçe başlığı yoktu: listede `"bir saniye"` yazıyordu, gerçek
başlık ise **`"Bir dakika lütfen..."`**.

**Sonucu:** Bot Türkçe CF challenge'ını hiç tanımıyordu. `_cf_gec()` "geçildi"
sanıp taramaya başlıyor, `_sayfa_durumu()` 'bos' diyordu. 18-19.09'da
"ilk açılış temiz geçti" diye kaydedilen gözlemlerin bir kısmı aslında
**challenge ekranının üstünde tarama yapmaktı** — o yüzden o tarihlerdeki
"CF görmedik" notlarına güvenme.

**Düzeltme:** `CF_TITLE_ISARETLERI` tek kaynak + `_cf_title_mi()` yardımcısı;
dört çağrı yeri de buna bağlandı. Sentetik test (`durum_testi.py` mantığı,
gerçek sahada gözlenen title'larla) 9/9 geçiyor, yanlış pozitif testi dahil
(gerçek liste sayfası title'ları `ok` kalıyor).

### Ölçülen gerçekler (tahmin değil)

Tüm logların analizi (`offset_block_analiz.py` mantığı — dikkat: ilk
versiyonunda attribution bug'ı vardı, "DB:" satırı tur sonunda bir kez
geldiği için başarılar son sayfaya yazılıyordu; düzeltilmiş hali her
sayfanın sonucunu bir sonraki olaya bakarak belirler):

```
 offset |    ok  block  bos | toplam  başarısız
      0 |  1255     20    4 |   1279        2%
     20 |   652     14    4 |    670        3%
     40 |   471     11    3 |    485        3%
  GENEL |  2901             |   2957        2%
```

**Tarama döngüsü %98 çalışıyor.** "Sürekli engelleniyoruz" algısı yanıltıcı.

Günlük veri girişi:

```
18.09 → 2443 yeni ilan   ← "CF cehennemi" yaşanan gün, EN VERİMLİ gün
17.09 → 2124
16.09 → 1303
```

Yani block'lar Faz 1'i (veri toplama) durdurmuyor. Asıl maliyet veri
kaybı değil, **kullanıcının bota bakıcılık yapması**.

### Geriye kalan en iyi açıklama

Kesin kanıt yok, ama tüm kanıtlarla tutarlı olan: sahibinden'in
`/otomobil` arama endpoint'inde **oturum/IP başına bir istek bütçesi**
var. Bot birkaç dakika sorunsuz çalışıyor, bütçe dolunca sabit bir küçük
sayfa (11.460 / 12.595 byte — aynı boyut tekrar tekrar) dönmeye başlıyor
ve bir süre böyle kalıyor. Elle gezen kullanıcı bu bütçeyi zorlamıyor
(dakikalar süren okuma), bot ise turda 2-3 arama sorgusu atıyor.

**"Basılı tut" challenge'ı neden bazen kabul etmiyor:** Turnstile bir
beceri testi değil, arka planda tarayıcı ortamını puanlar. "Tekrar dene"
demesi yanlış bastığın anlamına gelmez, ortamı beğenmediği anlamına
gelir. Bu yüzden "daha iyi basmak" diye bir çözüm yok.

### ✅ KESİNLEŞTİ (21.09.2026): Sadece PerimeterX, Cloudflare hiç devrede değil

20.09'daki bulgu 21.09'da tam kanıtla doğrulandı — 5 ayrı `duvar_dump`
dosyası, tüm bilinen imza listeleriyle taranarak:

- **PerimeterX kanıtı:** `px-cloud.net` (10x), `px-captcha` (280x), uygulama
  ID'si `PXQerrWGjI` (10x), `captcha.px-cloud.net` (5x) — hepsi gerçek script
  kaynak URL'lerinde ve CSS sınıf adlarında.
- **Cloudflare kanıtı:** **SIFIR.** `turnstile` kelimesinin tek eşleşmesi
  tanı scriptinin kendi başlık satırıydı (`--- turnstile/challenge/captcha/
  cf- GECEN SATIRLAR ---`), gerçek sayfa içeriğinde değil — kendi yanlış
  pozitifimiz. `cf_clearance`, `__cf_bm`, `cf_chl_`, `challenges.cloudflare.com`
  hiçbiri hiçbir dosyada yok.
- Bulunan tek CF referansı `cloudflareinsights.com` — bu Cloudflare'in bot
  korumasıyla **ilgisiz** ayrı bir ürünü (Web Analytics/RUM beacon'ı),
  hemen hemen her sitede bulunur, kanıt sayılmaz.

**Sonuç: sahibinden'in `/otomobil` engeli tek katman — PerimeterX/HUMAN
Security. Cloudflare hiçbir rol oynamıyor.** İki günlük "Cloudflare title'ı
bul" çabası (CF_TITLE_ISARETLERI, Türkçe "Bir dakika lütfen" fix'i) yanlış
hedefe çalışıyordu — o kod zararsız duruyor (gerçek bir CF sitesinde işe
yarar) ama BU sorunla hiç ilgisi yokmuş.

### 🎯 GERÇEK KİMLİK BULUNDU: PerimeterX/HUMAN, Cloudflare DEĞİL (20.09.2026)

İki gündür "Cloudflare CF challenge" diye avladığımız "basılı tut" ekranı
**hiç Cloudflare değilmiş.** `durum != 'ok'` her olduğunda ham title + HTML'i
diske döken bir tanı eklentisiyle (`_duvar_dump_yaz`) yakalandı:

```
title : 'Access to this page has been denied'
<script src="/QerrWGjI/captcha/captcha.js?...">
<script src="https://captcha.px-cloud.net/PXQerrWGjI/captcha.js?...">
<div class="px-captcha-header">Bağlantınız kontrol ediliyor...</div>
<div class="px-captcha-message">Devam edebilmek için lütfen aşağıdaki
    butona basılı tutun.</div>
```

`px-cloud.net`, `px-captcha`, `PXQerrWGjI` — bu **PerimeterX/HUMAN Security**,
kurumsal seviyede ayrı bir bot-tespit ürünü. `cloudflareinsights.com` referansı
(daha önce görülen) sadece bir analitik beacon'ı, asıl engelleyici o değil.

**Bunun anlamı:**
- İki günlük "Cloudflare title'ı bul" arayışı (CF_TITLE_ISARETLERI, "Bir
  dakika lütfen" fix'i) **yanlış hedefe** çalışıyordu — o kod hâlâ doğru
  (gerçek CF challenge'ları için gerekli) ama bu spesifik "basılı tut"
  sorununu hiç çözmüyordu, çünkü o zaten CF değildi.
- Bu ekranın title'ı (`"Access to this page has been denied"`) HTML boyutu
  küçük olduğu için (`<50KB`) `_sayfa_durumu()` tarafından **`block`**
  sanılıyordu — yani "sunucu düpedüz reddediyor, kimsenin çözebileceği bir
  şey değil" muamelesi görüyordu. **Yanlıştı.** İçinde gerçekten tıklanıp
  basılı tutulabilen bir düğme var, kullanıcı onu manuel çözebiliyor —
  ama kod bunu hiçbir zaman "burada bir düğme var" diye söylemiyordu,
  sessizce 2-5dk bekleyip tekrar deniyordu.
- PerimeterX'in "Press & Hold" tasarımı **kasıtlı olarak** basma süresini/
  mikro-hareketleri ölçer — "bazen tek seferde geçiyor bazen defalarca
  süründürüyor" gözlemi burada açıklanıyor: normal tıklamadan farklı,
  sürekli girdi kalitesini puanlayan bir mekanizma.
- **Proxy failover'ı da yanlış tetikleniyordu**: bu captcha 'block' sanıldığı
  için `PROXY_BLOCK_ESIK`'e sayılıyor, proxy'yi "kötü" damgalayıp
  değiştiriyordu — oysa bu IP'ye özgü bir red değil, oturum/tarayıcı
  seviyesinde bir doğrulama. Proxy değiştirmek hem gereksiz hem de yarı
  çözülmüş bir challenge'ı sıfırlayabilir.

**Düzeltme (`_sayfa_durumu`, yeni durum `'captcha'`):**
- Title tam olarak `"access to this page has been denied"` → `'captcha'`
  (CF/giriş/2fa gibi SADECE title bazlı, body-content aramasi YOK —
  turnstile/cloudflareinsights.com yanlış pozitif dersine sadık kalındı)
- Ana döngüde ayrı dal: proxy/block sayacını **etkilemiyor**, pencereyi
  gösterip kullanıcıya net talimat veriyor ("Press & Hold düğmesine bas"),
  `_sayfa_duzeldi_mi()` ile istek atmadan bekliyor, çözülünce hemen devam.
- Test: `captcha_testi.py` mantığı, gerçek yakalanan HTML ile — 6/6 geçti,
  eski sınıflandırmalar (cf/giriş/2fa/ok/block) bozulmadı.

**Ne YAPILMADI ve yapılmayacak:** Bu düğmeyi otomatik "basılı tutacak" bir
JS/CDP simülasyonu yazılmadı — PerimeterX'in davranış puanlamasını taklit
etmeye çalışmak captcha atlatma/evasion sınırına girer. Sadece doğru
tanıyıp kullanıcıya doğru bilgi vermek ve boşuna istek atmamak yapıldı.

### Geçmiş block'ların gerçek kimliği + sıklık analizi (20.09.2026)

Tüm proje geçmişindeki 106 "BLOCK" olayının **%91'i (96 tanesi) tam olarak
PerimeterX captcha'sının boyut aralığında** (10-14KB), 56'sı byte'ı byte'ına
aynı (11.460) — yani neredeyse hepsi bu TEK duvarmış, farklı olaylar değil.

**Tur numarasıyla korelasyon YOK** (tur 1-2: %22, tur 20+: %17 — düz
dağılım). Bu, "uzun süredir açık oturum güveni aşındırıyor" hipotezini
çürütüyor — periyodik oturum/tab yenileme muhtemelen işe yaramaz, sadece
daha fazla soğuk başlangıç üretir. Model muhtemelen oturum yaşından
bağımsız, istek başına ~%10 taban ihtimalli bir risk skoru.

**Denenen sonraki adım:** `_insan_gibi_davran()`'daki fare hareketi
zenginleştirildi — eskiden %40 ihtimalle TEK bir "ışınlanma" hareketi
vardı (`move_to_element` tek çağrı), şimdi her scroll civarında birden
fazla ilana ara adımlarla uğrayan, küçük gecikmeli hareket dizisi var
(`_mikro_mouse_hareketleri`). Gerekçe: PerimeterX/HUMAN ürünleri sürekli
mouse telemetrisine ağırlık verir, tek hareketlik bir oturum bunun aksine
çok belirgin bir sinyal. **Henüz ölçülmedi** — birkaç saatlik veri
birikince block/captcha sıklığı öncesi/sonrasıyla karşılaştırılacak.

### Operasyonel karar (19.09)

- **Hipotez avı durduruldu.** Her restart yeni bir açılış challenge'ı
  demek; 18-19.09'da ~12 restart yapıldı, kullanıcıyı yoran esas şey buydu.
- Bot kendi haline bırakılacak, günde bir kez log'a bakılacak.
- Sürekli block durumuna girerse (3-4 üst üste): kapat, birkaç saat
  bekle, akşam yeniden başlat. Her 5 dakikada bir denemeye devam etmek
  durumu uzatıyor.
- **Kalıcı profil:** `brave_oto_profile_v2` (proje kökünde, ~170MB).
  Bozulduğundan şüphelenirsen klasörü sil, bot yenisini oluşturur ve
  challenge bir kez daha elle çözülür.

### 19.09'da kodda kalan değişiklikler

- `_cache_bust()` **silindi** (deterministik bot imzasıydı)
- `_insan_gibi_davran()` — okuma beklemesi + scroll + mouse. Scroll
  `execute_script` yerine `ActionChains.send_keys(PAGE_DOWN)` ile yapılıyor
  (JS ile tetiklenen scroll `isTrusted=false` işaretlenir)
- `_block_cozuldu_mu()` — block'ta kör uyumak yerine, YENİ İSTEK ATMADAN
  açık sayfayı 15sn'de bir izler; kullanıcı challenge'ı çözerse hemen devam
- Block molaları: 30dk/2sa/6sa → **2dk/3dk/5dk** (kullanıcı talebi: saatlerce
  bekleme yok, gerekirse elle müdahale eder)
- CF kontrol aralığı: 60sn/5dk → **20sn/30sn** (elle çözülen challenge
  hızlı yakalansın)
- `switch_to.window()` artık sadece birden fazla sekme varsa çağrılıyor —
  Windows'ta bu çağrı pencereyi öne atıp kullanıcıyı rahatsız ediyordu

---

## 💣 KÖK NEDEN: `Preferences` dosyası katlanarak şişiyordu (22.09.2026)

Günlerdir "PerimeterX yüzünden" sanılan kararsızlığın **büyük kısmı aslında
bir kodlama (encoding) bug'ıymış.** Saha bulgusu: Brave hiç açılamaz oldu
(`session not created: cannot connect to chrome`). Sebep, profildeki
`Default/Preferences` dosyasının **3 GB** olması.

### Mekanizma

`undetected_chromedriver/__init__.py` (satır ~428) "restore-tabs-nag"
bayrağını düzeltmek için Preferences'ı şöyle açıyor:

```python
with open(..., encoding="latin1", mode="r+") as fs:
    config = json.load(fs)
    ...
    json.dump(config, fs)      # ensure_ascii=True (varsayılan)
```

Profilin adı **"Kişisel"** idi. Adım adım:

1. Brave dosyayı UTF-8 yazar: `ş` → `C5 9F` (2 bayt)
2. uc `latin1` ile okur → `Å` + `Ÿ` (2 ayrı karakter)
3. `json.dump` ASCII'ye kaçışlar: `Å\u009f` (12 bayt metin)
4. Brave bunu okuyup UTF-8 yazar → 4 bayt
5. Sonraki açılışta uc tekrar latin1 okur → 4 karakter → 8 bayt...

**Her Brave açılışında bayt sayısı İKİYE KATLANIYOR.** Ölçülen son değer
`profile.name` için 16.777.222 karakter = tam 2²⁴ — katlanmanın kesin
kanıtı. ~20 açılış sonra dosya GB ölçeğine çıkıyor ve Brave ölüyor.

### Neden PerimeterX sanıldı

Şişmiş dosya GB'a varmadan önce de zarar veriyordu: Brave her açılışta
onlarca MB'lık bozuk JSON'u ayrıştırmaya çalışıyor, chromedriver komutları
**120 saniyede cevap alamıyordu**. Bu da log'a `ReadTimeoutError` olarak
düşüyordu — ve `urllib3.exceptions.ReadTimeoutError` bir `WebDriverException`
DEĞİL, yani koddaki driver-kurtarma dalına hiç girmiyordu. Bot asılı
driver'la tekrar deniyor, her deneme 120sn + 15sn yakıyordu.

Ölçüm (21-22.09, 143 tur): **turların %64'ü verisiz.** Boş turların
%65'inde log'da hiçbir engel sebebi yoktu — hepsi bu timeout zinciriydi.

### Düzeltme (üç katmanlı)

1. **`profile.name` → `"Oto Bot"`** (ASCII). Türkçe karakter olmayınca bug
   tetiklenemez. Dosya 32 MB → 21 KB. Yedek: `Preferences.yedek_20260922`.
2. **uc kütüphanesinde `encoding="latin1"` → `encoding="utf-8"`.**
   ⚠️ Bu `site-packages` içinde — `pip install -U undetected-chromedriver`
   yapılırsa **kaybolur ve bug geri gelir.** Güncelleme sonrası tekrar
   uygulanmalı.
3. **`ReadTimeoutError` artık yakalanıyor** — mesajda `"Read timed out"` /
   `"HTTPConnectionPool"` geçiyorsa driver yeniden kuruluyor. Ayrıca
   `driver.quit()` arka plana alındı (`_arka_planda_kapat`), çünkü asılı
   bağlantıda quit'in kendisi de 120sn blokluyordu.

### İzleme

`Default/Preferences` normalde **~20 KB**. Periyodik olarak bakılmalı;
1 MB'ı geçtiyse bug geri gelmiş demektir (muhtemelen uc güncellenmiştir).

---

## 22.09.2026 — diğer değişiklikler

- **Engel ekranlarında proxy DEĞİŞMİYOR** (kullanıcı kararı). Açılış
  kontrolü, `px_hard` ve `block` dallarının üçünde de failover kaldırıldı.
  Gerekçe: üç proxy de sırayla hard block yiyip ~50sn'de bir Brave'i
  yeniden başlatıyordu — veri sıfır, IP'ler daha çok yanıyordu. Proxy
  artık sadece başarılı tarama sırasında, tur sonu aralığında değişiyor.
- **PX karantinası kaldırıldı**: `PROXY_HARD_KARANTINA_SN` 45dk/2sa/6sa →
  sabit 30sn. `PROXY_DINLENME_SN` 30dk → 30sn.
- **Otomatik PX çözümü (`_px_coz`) TAMAMEN DEVRE DIŞI.** Ölçüm: tüm log
  geçmişinde **33 deneme, 0 başarı**, 12 kez "iframe hiç açılmadı". Her
  olayda 3 deneme × ~10sn boşa gidip zaten kullanıcıya bırakılıyordu.
  Fonksiyon dosyada duruyor ama hiçbir yerden çağrılmıyor. PX'i kullanıcı
  elle geçiyor — bu doğrulanmış tek yöntem.
- **Derinlik planı genişledi**: `(20,2)` → `(20,1)`, yeni `(60,4)` eklendi.
  Gerekçe ölçüldü: yoğun saatte (12:00-15:00) sahibinden'e **saatte ~250-300
  yeni ilan** giriyor, yani sayfa 1 (20 ilan) **4 dakikada** tamamen
  yenileniyor. Tur periyodu 3-5 dk olduğu için sayfa 1 tek başına başabaştı;
  bir gecikme = kalıcı veri kaybı. Sayfa 2 her tura alınınca 2 kat pay oldu.

### Reddedilen öneri: 15-30sn'lik "hızlı izleme" katmanı

Öneri, tur periyodunu 15-30sn'ye indirip PX'siz hafif bir endpoint
kullanmaktı. Uygulanmadı, iki sebeple:

1. **Kendi içinde çelişkili:** aynı öneri "PX hız bazlı (rate-based)
   blokluyor" tespitini yapıp ardından istek hızını ~7 katına çıkarmayı
   öneriyordu (saatte ~27 → ~180 istek).
2. **Yanlış problemi çözüyor:** ölçüm, sorunun *gecikme* değil *kapsama*
   olduğunu gösterdi. Araç ilanı 5 dakikada satılmıyor; asıl risk ilanı
   hiç görmemek. Kapsama ise derinlikle çözülür (saatte ~39 istek), sıklıkla
   değil (saatte ~180).

Tur periyodu bilerek **180-300sn'de bırakıldı** — o bekleme boşa değil,
istek hızını düşük tutan tek mekanizma.

---

## 23.09.2026 — Proxy ölçümü: "işe yaramıyor" sanılıyordu, yanlıştı

Kullanıcı gözlemi: "proxy bir işe yaramıyor, PX yine geliyor." Doğru gözlem,
yanlış sonuç. Ölçüm (aynı gün, aynı kod, tek değişken proxy):

```
                      PROXY'Lİ          PROXYSİZ
                   16:34→17:06        17:32→17:44
süre                  32 dk             12 dk
tur sayısı            6                 6
YENİ İLAN             179               0        ← kritik fark
chromedriver timeout  1                 4
PX olayı              2                 1
```

**Proxy PX'i engellemiyor — ama çalışmayı mümkün kılıyor.** İkisi farklı şey.
Proxysizken her tur ilk sayfa isteğinde asılıyor (~230-245sn sonra timeout),
restart sonrası anında `PX HARD BLOCK (acilis)` geliyor. Ev/mobil IP'nin PX
tarafından ağır damgalandığı anlaşılıyor — `.env`'deki eski not da bunu
söylüyordu: *"PerimeterX ev IP'sini damgaladigi icin proxy ZORUNLU."*

**Ayrıca çürüyen teori:** "chromedriver takılmalarının sebebi residential
proxy'nin TCP bağlantısını yarım bırakması" (22.09 gecesi kuruldu). Proxysiz
dönemde takılma **4 katına çıktı** → proxy kaynaklı değilmiş. Kalan en iyi
açıklama: sayfa PX challenge döndürüyor, yükleme hiç bitmiyor, chromedriver'a
giden komutlar asılı kalıyor.

**Kullanıcı kararı (23.09):** buna rağmen proxysiz devam. Sonuç: Faz 1 veri
toplama fiilen durdu. Geri açmak için `.env`'de `PROXY_LIST` satırındaki `#`
kaldırılır (yedek: `.env.yedek_20260923`).

### Profilde kalıcı proxy izi — gizli tuzak

Proxy `.env`'den silinmiş, `--proxy-server` argümanı verilmemiş ve Windows
sistem proxy'si kapalı olmasına rağmen Brave **hâlâ proxy şifresi soruyordu.**
Sebep: proxy auth uzantısı `chrome.proxy` API'siyle ayarı profile YAZMIŞ ve
bu `Default/Secure Preferences` içinde kalıcı olmuş:

```
extensions.settings.nengbjhcmaglhaldldipihghggiplibc.preferences.proxy
```

`Preferences` ve `Local State` temizdi — ayar sadece `Secure Preferences`'taydı.
Uzantı girdisi + MAC imzası silinerek temizlendi (yedek:
`Secure Preferences.yedek_20260923`). **Ders: proxy'yi kaldırırken profildeki
`Secure Preferences` de kontrol edilmeli**, yoksa hayalet proxy ayarı kalır.

### Ölü tarayıcıda sonsuz bekleme bug'ı

`_login_duvari_asildi_mi` ve `_sayfa_duzeldi_mi` içindeki `except Exception:
continue` koşulsuzdu. Kullanıcı pencereyi kapatırsa (veya Brave çökerse) bot
**ölü bir driver'ı 24 saat** izlemeye devam ediyordu — log sessiz, veri sıfır,
kendiliğinden kurtulmuyordu. Sahada aynen gözlendi. Düzeltme: istisna anında
`_driver_ayakta_mi()` kontrolü; tarayıcı ölmüşse hemen `False` dönülüyor ve
ana döngü yeni driver kuruyor.

---

## 🎯 23.09.2026 AKŞAMI — ASILMANIN KÖK NEDENİ BULUNDU

Günlerdir "PerimeterX bizi engelliyor" diye okunan tablonun büyük kısmı
aslında **kendi kodumuzun ürettiği bir asılmaydı.** Zincir ölçümle kuruldu:

```
_px_erken_sinyal() CDP fare olayı gönderiyor
   → sayfa hâlâ yükleniyorken renderer cevap vermiyor
   → komut asılı kalıyor
   → bot "driver öldü" deyip Brave'i yeniden kuruyor
   → YENİ OTURUM
   → PX yeni oturumu denetliyor → challenge
   → kullanıcı elle geçiyor
```

### Kanıt 1 — asılma nerede oluyor (koda konan ölçüm)

Log'u regex'le eşeleyerek çıkarım yapmak sınıra dayandı; koda tek satırlık
bir kayıt eklendi (istisnanın `oto_bot.py`'ye ait son karesi loglanıyor).
İlk iki asılmanın **ikisi de aynı satır**:

```
[ASILDIGI YER: _px_erken_sinyal() satir 3457:
              driver.execute_cdp_cmd("Input.dispatchMouseEvent", {]
```

`_px_erken_sinyal`, sayfa yüklenirken PX'in skorlama penceresine "organik
telemetri" göstermek için 3-6 CDP fare hareketi + tekerlek olayı gönderiyor.
Renderer meşgulken bu komutlar cevapsız kalıyor.

### Kanıt 2 — PX'in %100'ü tarayıcı açılışında

Bugünkü 7 PX olayının tamamı, Brave yeniden açıldıktan sonra **daha tek tur
dönmeden** geldi. Bot tur sayacına göre dağınık (#0,#0,#1,#1,#4,#6,#10) ama
açılışa göre hizalayınca desen mutlak:

```
açılıştan sonra 0. tur : 7/7  (%100)
öncesinde timeout olan : 5/7  (%71, hepsi ~20 sn önce)
```

**Sağlıklı bir oturum saatlerce tarasa PX görmüyor.** PX'i azaltmanın yolu
PX'le uğraşmak değil, tarayıcının asılmasını engellemek.

### Kanıt 3 — CDP navigate vs düz driver.get()

47 asılmanın 47'si CDP `Page.navigate` yolunda, 0'ı düz `driver.get()`
yolunda gerçekleşti. Sebep: CDP navigate "gönder ve unut" çalışır, Selenium
navigasyonu izlemez → `set_page_load_timeout` UYGULANMAZ → sonraki DOM
sorgusu süresiz bloklar. `PX_CDP_NAVIGATE=0` yapıldıktan sonra:

```
sayfa süresi medyanı : 227 sn → 29 sn
60 sn altında kalan  : 0/13  → 4/6
en hızlı sayfa       : 80 sn →  9 sn
```

### Uygulanan düzeltmeler (23.09)

| # | Değişiklik | Ölçülen etki |
|---|---|---|
| 1 | `_baglanti_koptu_mu()` — bağlantı koptuysa katmanlar hatayı yutmuyor, ilk fark eden fırlatıyor | tespit 256sn → 52sn |
| 2 | `PX_CDP_NAVIGATE=0` — düz `driver.get()` | sayfa medyanı 227sn → 29sn |
| 3 | `set_page_load_timeout(30)` her driver kurulumunda | (önce SADECE proxy doğrulamasında çağrılıyordu; proxysizken hiç uygulanmıyordu) |
| 4 | Asılma yeri log'a yazılıyor | kök neden bulundu |
| 5 | Ölü tarayıcıda sonsuz bekleme kapatıldı | 24 saat → 1 sn |

### ⛔ SIRADAKİ ADIM: `_px_erken_sinyal` KALDIRILMALI

Asılmaların doğrudan sebebi bu fonksiyon ve **faydası hiç kanıtlanmadı.**
Amacı PX'in davranış puanlamasına yükleme sırasında sahte fare telemetrisi
beslemek. `_px_coz` ile aynı kategoride (o da 33 denemede 0 başarıyla
kaldırıldı). Çağrıları devre dışı bırakılmalı, fonksiyon dosyada kalabilir.

Beklenen zincir: asılma ↓ → restart ↓ → **PX ↓** (çünkü PX'in %100'ü
restart sonrası geliyor).

### Ölçüm disiplini — bugün 3 kez yanıldım, sebebi aynıydı

1. "Preferences şişmesi timeout'ları açıklıyor" → düzeltildi, oran değişmedi
2. "Bozuk proxy 18178" → kullanım yanlılığı düzeltilince fark kalmadı
3. "0 ilan toplandı" → **veritabanında 41 ilan vardı**

Üçüncüsü en kritik ders: kod her SAYFADAN sonra commit eder ama
`DB: yeni=...` özet satırını TUR SONUNDA basar. Tur timeout'la kesilince
özet satırı yazılmaz, veri ise kaydedilmiştir. **Log'dan veri sayma.**

**KURAL:** veri soruları veritabanından, olay soruları log'dan cevaplanır.
Log olayları tekilleştirilmeli (bot aynı olayı 2 kez yazıyor).

### `durum.py` — tek kaynak durum raporu

Bu kural koda gömüldü. `python durum.py [dakika]` → process durumu, DB'den
veri akışı, tekilleştirilmiş olaylar, "şu an ne yapıyor" ve sağlık uyarıları
(Preferences şişmesi, profilde hayalet proxy izi, çoklu instance).

### Günlük veri girişi

```
16.09  1300     20.09  1541
17.09  2122     21.09  1118
18.09  2438     22.09   776
19.09  1171     23.09   611  (bot günün çoğunda kapalıydı/test ediliyordu)
```

---

## Rafa Kaldırılan İşler (sonra geri gelecek)

Faz 1'de denendi, gerçekçi çalışmadığı görülünce durduruldu:

### 🚫 Ölüm takibi (yeniden Faz 3-4'te)
"3 tur görülmedi → satıldı" mantığı, sahibinden'in trafik saatinde
sayfa dinamiği yüzünden **hayalet ölümler** işaretledi (6098 ilanın
hepsi 0.0 gün yaşamış görünüyordu). Doğru sinyal için ilanın **detay
sayfasında** hâlâ orada mı bakmak gerek — Faz 3'te detay çekildikten
sonra.

`durum`, `olum_tarih`, `yasam_gun`, `olum_tipi`, `gorulmedi_sayac`
kolonları şemada duruyor, kod yazmıyor.

### 🚫 Fiyat düşüş sinyali (yeniden Faz 4'te)
Sahibinden **ilan_id'yi yeniden kullanıyor** — satılan/kapanan bir
ilanın ID'si başka bir araca atanınca "7.4M → 390k %94 düşüş" gibi
hayalet sinyaller çıktı. `fiyat_gecmisi` tablosu her fiyat değişiminde
INSERT ediyor (ham veri korunur), ama `dusus_sayisi`,
`toplam_dusus_yuzde`, log sinyali **kapalı**.

**ID reuse detection eklendi:** eğer aynı ilan_id'de fiyat >%50
değiştiyse eski satır **`ilan_arsiv`**'e kopyalanır (yasam_gun ile
birlikte), sonra ilan yeni araca güncellenir.

**Bu KESIN ölüm sinyalidir** — hayalet değil. `ilan_arsiv` likidite
ölçümünün en güvenilir kaynağı: "Bu bucket'ta araçlar medyan kaç günde
satılıyor?" sorusunun cevabı burada. Faz 4'te bucket başına
`medyan_yasam_gun_arsiv` üretilecek.

---

## Oto'ya Özel 6 Tuzak

| # | Tuzak | Tespit |
|---|---|---|
| 1 | Hasarlı/pert normal ilanda | Detay: ağır hasar alanı + boya şeması (Faz 3) |
| 2 | Yurt dışı / gümrüksüz / yabancı plaka | Plaka alanı + regex (Faz 3) |
| 3 | Senetli / vadeli satış | Açıklama regex (Faz 3) |
| 4 | **Galerici yem ilanı** ⚠️ en sinsi | `satici_id` + ilan yaşı + kara liste (Faz 4) |
| 5 | Km yalanı | Yıla göre absürt düşük km → şüphe |
| 6 | Donanım paketi farkı | **L1 bucket (motor+paket) ayırır** ✓ |

---

## Botun YAPAMAYACAKLARI (dürüst sınır)

```
❌ Tramer kaydı        (sahibinden göstermiyor, sorgu gerekir)
❌ Km yalanı           (sadece şüphe işaretler)
❌ Motor/şanzıman durumu
❌ Var olmayan yem ilan (sadece olasılık verir)
```

Bot **"gidip bakmaya değer mi?"** sorusunu cevaplar. **"Al" demez.**

---

## Veri Modeli (mevcut, Faz 1 v3 sonu)

```sql
-- Ham liste verisi
CREATE TABLE ilan (
    ilan_id       TEXT PRIMARY KEY,
    marka         TEXT,
    seri          TEXT,
    model         TEXT,       -- ham string ("1.6 TDI BlueMotion Comfortline")
    motor_hacim   REAL,       -- parse edilmiş (1.6)
    motor_tipi    TEXT,       -- parse edilmiş ("tdi")
    paket         TEXT,       -- parse edilmiş ("comfortline")
    arac_sinifi   TEXT,       -- 'sifir' / 'ikinci_el'
    yil           INTEGER,
    km            INTEGER,
    yakit         TEXT,       -- Faz 3: detaydan
    vites         TEXT,       -- Faz 3: detaydan
    renk          TEXT,       -- Faz 3: detaydan
    fiyat         REAL,
    il, ilce      TEXT,
    kimden        TEXT,       -- 'galeriden' / 'sahibinden'
    satici_id     TEXT,       -- store subdomain
    satici_ad     TEXT,
    satici_url    TEXT,
    ilan_tarih    TEXT,
    baslik        TEXT,
    url           TEXT,
    gorsel        TEXT,
    attr_ham      TEXT,       -- JSON: tag+attr hücreleri
    ilk_gorulme   TEXT,
    son_gorulme   TEXT,
    son_fiyat     REAL,
    ilk_fiyat     REAL,       -- fiyat takibi ham (Faz 4)
    guncel_fiyat  REAL,
    ilan_yasi_gun REAL,
    -- Aşağıdakiler rafta:
    durum, olum_tarih, olum_tipi, yasam_gun, gorulmedi_sayac,
    dusus_sayisi, toplam_dusus_yuzde, son_dusus_tarih
);

-- Fiyat değişim geçmişi (Faz 4'te sinyal analizi için)
CREATE TABLE fiyat_gecmisi (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ilan_id TEXT NOT NULL,
    fiyat   REAL NOT NULL,
    tarih   TEXT NOT NULL
);

-- Öğrenilen piyasa (kademeli bucket)
CREATE TABLE bucket_stat (
    bucket_key       TEXT PRIMARY KEY,   -- 'L1:vw|passat|1.6|tdi|comfortline|2018|ikinci_el|galeriden'
    katman           TEXT,               -- 'L1' / 'L2' / 'L3'
    marka, seri      TEXT,
    motor_hacim      REAL,
    motor_tipi       TEXT,
    paket            TEXT,
    yil              INTEGER,
    arac_sinifi      TEXT,
    kimden           TEXT,
    ort_fiyat        REAL,
    medyan_fiyat     REAL,
    min_fiyat        REAL,
    max_fiyat        REAL,
    ilan_sayisi      INTEGER,            -- uç değer korumalı
    medyan_yasam_gun REAL,               -- likidite (Faz 3-4)
    guncellendi      TEXT
);

-- Faz 3: sadece aday ilanlar için detay
CREATE TABLE detay (
    ilan_id          TEXT PRIMARY KEY,
    orijinal, boyali, lokal_boyali, degisen  INTEGER,
    agir_hasar       INTEGER,
    plaka_uyruk      TEXT,
    hasar_puani      REAL,
    kirmizi_bayrak   TEXT,
    aciklama_ozet    TEXT,
    okundu_tarih     TEXT
);

-- Faz 4: ÖĞRENME — sistemin geleceği
CREATE TABLE etiket (
    ilan_id    TEXT,
    kullanici  TEXT,
    karar      TEXT,       -- degerdi / vakit_kaybi / yem / aldim
    sebep      TEXT,
    tarih      TEXT
);

-- Faz 4: Satıcı istihbaratı (galerici yem ilanı tespiti)
CREATE TABLE satici (
    satici_id      TEXT PRIMARY KEY,
    ilan_sayisi    INTEGER,
    yem_sayisi     INTEGER,
    kara_liste     INTEGER,
    son_gorulme    TEXT
);
```

---

## Fazlar

```
FAZ 0 — KEŞİF (bitti)   ⚠️ ATLANMADI

FAZ 1 — TOPLAYICI (orta, veri hijyeni tamam)
  ✓ Tarama + parse + DB (WAL, indeksli)
  ✓ Motor/paket/hacim ayrıştırma (parser)
  ✓ Sıfır km / ikinci el ayrımı
  ✓ Kimden (galerici/sahibinden) ayrımı
  ✓ Kademeli bucket (L1/L2/L3)
  ✓ Uç değer koruması
  ✓ ID reuse detection
  ✗ Ölüm takibi   (rafta — Faz 3'te detayla)
  ✗ Fiyat düşüş sinyali (rafta — Faz 4'te)
  Şu an sadece veri toplama, bildirim YOK.

FAZ 2 — KM MODELİ (bekliyor)
  Bucket başına km regresyonu
  → EN AZ 2 HAFTA VERİ TOPLA, avcıyı AÇMA

FAZ 3 — DETAYCI (bekliyor)
  Boya/değişen parse, kırmızı bayraklar, hasar puanı
  Yakıt/vites/renk detaydan
  İlan ölümü doğrulama (detay sayfası hâlâ orada mı)

FAZ 4 — AVCI + TELEGRAM (bekliyor)
  Sapma hesabı, güven skoru, kademeli sinyal
  Fiyat düşüş sinyali (ID reuse detection ile birlikte)
  Yem ilan tespiti (satici tablosu)
  Etiketleme: Telegram inline butonlar
  [👍 Değerdi] [👎 Vakit kaybı] [🚫 Yem]

FAZ 5 — APP (sonra)
FAZ 6 — KALİBRASYON (sürekli)
```

---

## Teknik Notlar

- **Tarayıcı:** Brave + undetected_chromedriver
  (`--no-sandbox` KULLANMA — Cloudflare yakalıyor)
- **Brave sürümü:** auto-detect (`.env`'de BRAVE_VERSION_MAIN boş)
- **Profil:** KALICI — `brave_oto_profile_v2` (19.09'dan itibaren).
  17.09'daki "anonim/geçici profil" kararı 19.09'da geri alındı, sebebi
  "CF teşhisi: ölen teoriler" bölümünde. Bozulursa klasörü sil.
- **Pencere:** minimize (taskbar'da) — CF gelirse görünür olsun, ekran dışına ATMA
- **CF detection:** title-based ("Just a moment..." kontrol) —
  page_source aramasi yanlış pozitif veriyordu (sahibinden'in
  `static.cloudflareinsights.com` linki her sayfada var)
- **Tur periyodu:** SABİT 120-180sn (mola = hedef - geçen)
- **Sayfa:** turda 5 sayfa × 20 ilan = ~100 ilan (env'den `MAX_SAYFA`)
- **Cache-bust:** KALDIRILDI (18.09) — `?cb=rand` normal kullanıcıda
  olmayan deterministik bir bot imzasıydı
- **WebDriverWait:** ilan render'ı beklenir, sabit sleep yok
- **R2 self-heal:** driver ölürse yeniden kur (`window_handles` kontrolü)
- **RotatingFileHandler:** `oto.log` 2MB'da rotate, 3 backup
- **DB:** `oto_hafiza.db`, WAL modu, baştan indeksli
- **Python:** 3.12

---

## Donanım Projesinden Taşınacak Dersler

| Ders | Uygulama |
|---|---|
| **Telegram ana döngüyü bloklamamalı** | Kuyruk + worker thread (Faz 4) |
| **DB: WAL + indeks** | Baştan kur ✓ |
| **Sayfa yükleme: fetch yöntemi** | (Faz 3'te detay için) |
| **Bot sessizce ölür** | R2 self-heal ✓ |
| **Sıkışık eşleştirme** | Parser normalize (bosluk vs) ✓ |
| **Anomali istatistiği kirletir** | Uç değer koruması ✓ |
| **Etiketleme olmadan öğrenme yok** | `etiket` tablosu Faz 4'te aktif |
| **Önce ÖLÇ, sonra optimize et** | Rapor'a bakıp parser genişletildi ✓ |

---

## Durum (16.09.2026 — v4)

**Şu an: FAZ 1 sürüyor, veri hijyeni + kategorizasyon TAMAM.**

- ~7800 ilan toplandı, hepsi aktif, veri sağlıklı
- Motor/paket parser: **%83 L1 uygun** (nasp/standart default ile)
- Bucket sayısı: L1=2767, L2=2091, L3=1776 → **toplam 6634**
- Anlamlı bucket (n≥8): **L1=120, L2=184, L3=282**
- ilan_arsiv: ID reuse tespitiyle 2 kesin ölüm sinyali (Faz 4 için)

**v4 kritik iyileşmeler:**
- `kimden` bucket key'den çıktı → aynı araç bölünmez, bucket içinde 3 agrega
- `motor_tipi='nasp'` + `paket='standart'` default'ları — L1 %36→%83
- `OLUM_TAKIBI_AKTIF=False` sabiti — ölü kod her turda çalışmıyor
- `ilan_arsiv` — ID reuse hayalet değil kesin ölüm

**17.09.2026 ek durum:** Ana tarama kademeli-offset fix'i uygulandı, kısmen
iyileşti ama tam çözmedi. **Detay worker `DETAY_WORKER_AKTIF=False` ile
geçici kapatıldı** — sadece ana tarama (liste sayfası) çalışıyor. Sebep:
detay worker'ı `fetch()`'ten `driver.get()`'e çevirmek block sorununu
çözmedi, muhtemelen mesele yöntem değil IP itibarı. Önce ana taramanın
uzun süre stabil kalmasını izleyip IP'nin daha fazla soğumasını
bekliyoruz, sonra detay worker'a dönülecek.

**Sonraki adım:** Ana tarama ile 2 hafta veri toplama devam. Sonrasında
Faz 2 (KM regresyonu) + detay worker'ı yeniden değerlendirme.

---

# 24.09.2026 — FAZ 2 AÇILDI + DOĞRULAMA DÖNGÜSÜ

## 🚨 SENTETİK İLAN KEŞFİ — `sb2f` (en önemli bulgu)

Kelepir listesi hazırlarken 3. ve 5. adayın başlığı markasıyla tutmuyordu
("Passat" diyor, URL'i `fiat-...-egea`). Kazınca çıkan:

```
URL-marka uyumsuz satır           : 454  (%2.4)
hepsinin ilan no öneki            : 846*
846* satırların URL'inde 'sb2f'   : 513 / 513  (%100)
diğer 18.597 ilanda 'sb2f'        :   0        (%0)
```

Mükemmel ayraç. Ek kanıtlar:
- Başlıklar anahtar kelime salatası ("Bol Ekstralı", "Dosta Gidecek",
  "Son 3 gün"). 3+ kalıp içeren: 846*'da **%45**, diğerlerinde **%0**.
- `attr_ham` DB satırıyla tutmuyor: 846*'da **%17**, diğerlerinde **%0**.
- Her gün yenileri geliyor (24.09'da 12 tane).

Bunlar gerçek araç değil. Ne oldukları kesin bilinmiyor (platform
artefaktı veya tarayıcı tuzağı) ama bucket ortalamalarını kirletiyorlardı.

**Filtre (kelepir.py'de sabit):**
```sql
url NOT LIKE '%sb2f%' AND ilan_id NOT LIKE '846%' AND COALESCE(cop_mu,0)=0
```

⚠️ **DERS:** Aday listesi üretirken URL slug'ındaki marka ile DB
markasını karşılaştır. Bu kontrol olmasaydı sahte ilanlar "kelepir"
diye sunulacaktı.

## Alman premium motor kodu

BMW'de **%100**, Mercedes'te **%98** `motor_hacim_grup='bilinmiyor'` idi —
`320d` / `C 200 d` kodları `\d\.\d` regex'ine takılmıyor.

```python
RE_BMW = r"\b([1-8]\d{2})\s*([di])\b"        # 320d, 520i
RE_MB  = r"\b([A-Z]{0,3})\s*(\d{3})\s*(d|CDI|BlueTEC)?\b"   # C 200 d -> 200d
```

Kapsam: BMW %92, Mercedes %99. Bucket yayılımı (IQR/medyan):

```
Mercedes-Benz  0.202 → 0.164   (-19%)  gerçek kazanç
BMW            0.205 → 0.193   ( -6%)  marjinal
```

**Çürütülen hipotez:** "BMW bucket'ları bozuk olduğu için %-84'lük
saçma adaylar çıkıyor." Yanlış — BMW yayılımı (0.205) VW'den (0.230)
zaten kötü değilmiş. O adaylar bucket sorunu değil, sahte ilandı.

## Eşik %-35 → %-20

Hedef isabet değil **kapsam** (kullanıcı kararı). Sonuç:

```
eşik        ham   bayrak temiz   robust onaylı
%-35         42             40              21
%-25        223            215             156
%-20        468            440             314
```

**Kör markalar açıldı** — asıl kazanç:

```
Mercedes-Benz   0 → 17     Skoda    0 → 11     Honda    0 →  9
Toyota          0 →  4     Citroen  0 →  4     Peugeot  0 →  3
TOPLAM         21 → 314
```

Pazarlık payı %5 varsayılırsa %-20 havuzunun medyan etkin sapması
**%-30** oluyor, 99 ilan %-32'nin altına iniyor.

⚠️ **BEDELİ:** Boya fiyatı %10-15, ağır hasar %25-35 düşürür. %-35'te
"hasar bunu açıklayamaz" diyebiliyorduk; **%-20'de diyemeyiz.** Bu eşik
Faz 3'ü opsiyonel olmaktan çıkarıp **ÖN KOŞUL** yapar.

## Taban filtresi %-50

Sapma %-50'nin altı kelepir değil: o grubun medyan km'si 196.000,
medyan yılı 2016 — hasarlı/yem bölgesi. `SAPMA_TABAN = -50.0`.

## YENİ DOSYALAR

### `kelepir.py` — skorlama motoru
Ağ kullanmaz, tek başına çalışır (`python kelepir.py 20`). Yukarıdaki
kuralların hepsi docstring'de gerekçesiyle yazılı.

```
havuz (temiz, aktif, ikinci el) : 18,396
skorlanabilen                   : 10,337 (%56)  L1=6632 L2=2236 L3=1469
ADAY (eşik %-20)                : 314    VURGUN 3 · FIRSAT 66 · IZLE 245
```

### `takip.py` — kohort takibi (doğrulama döngüsü)

**Neden:** Kullanıcı 10 adayı elle açtı, **yarısı satılmıştı.** Bu en
güvenilir kelepir kanıtı — ama hiçbir yere kaydedilmiyordu.

**🔴 KONTROL GRUBU ŞART:** "Adaylarımızın %50'si gitti" tek başına
hiçbir şey söylemez. Rastgele ilanın da %50'si gidiyorsa model sıfır
değer katıyor demektir. Her aday için **aynı bucket'tan, sapması ~0,
km'si en yakın** bir ilan da takibe alınır.

```
Ölçülen şey: aday_gitme_oranı − kontrol_gitme_oranı
```

Not: "gitti" = "yayından kalktı", "satıldı" değil. Süre dolması/vazgeçme
**her iki kohortta da aynı oranda** olacağı için fark yine geçerli.

**🔴 BLOCK ASLA SATIŞ SAYILMAZ.** PX ekranı "ilan bulunamadı" da
yazabilir. `sayfa_durumu()` önce block'a bakar, emin olmadığında
`belirsiz` der ve **sayıma sokmaz**. 10 sentetik test geçti, kritik
olan: `PX + 'ilan bulunamadi' tuzağı → block`.

**Rapor Wilson güven aralığı kullanır** ve küçük örnekte karar vermeyi
reddeder. 4 senaryo test edildi:

```
aday %60 / kontrol %20  → "aralıklar AYRIK: fark anlamlı"        ✓
aday %30 / kontrol %30  → "CAKISIYOR: anlamlı fark YOK"          ✓
n=6                     → "karar için >=20 ölçülen gerekiyor"    ✓
10 block sonucu         → gitti=0                                 ✓
```

### `oto_bot.py` kancaları
- Sabitler: `TAKIP_AKTIF`, `TAKIP_TUR_ARALIGI=3`, `TAKIP_KOHORT_TUR=60`
- `_takip_kontrol()` + `_takip_kohort_ekle()`
- Ana döngü: **sadece temiz turda** (`tur_kesildi=False` noktasında)

**HIZ KASTEN DÜŞÜK:** Detay worker geçmişte yoğun istekle block yemişti.
Burada 3 turda bir, 1 aday + 1 kontrol → saatte ~4 istek. Block görürse
o turda hemen durur.

Kontrol günleri: 2, 4, 7, 11, 15. Sonra takipten düşer.

## Kullanım

```bash
python kelepir.py 20        # en iyi 20 aday
python takip.py ekle 40     # yeni kohort (aday + eşleşmiş kontrol)
python takip.py rapor       # doğrulama sonucu
python takip.py kuyruk      # bugün kontrol edilecekler
python durum.py 75          # bot sağlığı (son 75 dk)
```

## Durum (24.09.2026)

- **19.172 ilan** (18.439 temiz, 515 sentetik)
- İlk kohort: **40 aday + 35 eşleştirilmiş kontrol**, 24.09 02:30'da girdi
- İlk kontroller 26.09'da, ilk anlamlı karar ~08.10 (her kohortta 20+ ölçülen)
- Faz 2 modeli doğrulandı: 503/695 bucket sağlıklı eğim, medyan
  **%-1.87 / 10.000 km** — CLAUDE.md'nin %-1.5..-2.0 varsayımıyla uyumlu

**Sonraki:** (a) kohort sonucu bekle, (b) Faz 3 detaycı (artık ön koşul).

---

## 🚫 `etiket` TABLOSU RAFA KALDIRILDI (24.09.2026)

Kullanıcı itirazı: *"bot eğitimi için iyi gözükse de negatifte etki
edebilir. benzer türde işe yarayacak ilan gelirse es geçmiş oluruz."*

**Haklı, ve gerekçe daha da güçlü:** Etiket verisi **iki kat yanlı**
olurdu —

1. Sadece botun zaten aday gösterdiği ilanlardan oluşur. Botun göremediği
   kelepir hiç etiketlenemez → huni daralır, genişlemez.
2. Onların da sadece kullanıcının bakmaya vakit bulduklarından.

`takip.py` bu sorundan **yapısal olarak muaf**: sinyal objektif (ilan ya
yayında ya değil), kontrol grubu da ölçülüyor, ve otomatik.

**Kural:** Etiket ileride açılırsa SADECE "neden" notu olarak
("gittim, motor sesi kötüydü"). **Asla otomatik filtre olarak değil.**

---

## KOHORT TAKİBİ AYRI PROCESS'E TAŞINDI (24.09.2026)

### Neden — bot ölürse ölçüm de ölüyor

23-24.09 gecesi bot **01:44'te öldü, 10.5 saat kapalı kaldı** (sebep
logda yok). Takip botun içindeydi → o süre boyunca takip de ölüydü.
Kontrol günleri (2/4/7/11/15) botun uptime'ına bağlı olamaz, yoksa
ölçüm penceresi kayar ve kohort bozulur.

`takip_runner.py` — bağımsız process, kendi tarayıcısını açar, işini
yapar, kapanır. **Kazanç: izolasyon ve süreklilik.**

### ⚠️ ÖNCE BULUNAN BUG: takip tur periyodunu uzatıyordu

Bottaki ilk kanca mola hesabından ÖNCE çağrılıyordu. Ama `gecen`
değişkeni yukarıda (satır ~4675) ölçüldüğü için takibin süresi molaya
sayılmıyor, tur döngüsünün **üstüne** ekleniyordu:

```
takipsiz tur döngüsü: 324sn
takip 50sn → ESKI 374s (+50s)  |  YENI 324s (+0s)
takip 60sn → ESKI 384s (+60s)  |  YENI 324s (+0s)
günlük tur:  takipsiz 267  |  ESKI 254  |  YENI 267
```

Günde 13 tur kaybı = kaçan ilan. Düzeltme: takip molanın İÇİNDE çalışır,
süresi moladan düşülür (`TAKIP_MOLA_TABANI=30` tabanıyla). Ayrıca son
istekten sonraki bekleme kaldırıldı (mola tabanı zaten o boşluğu veriyor).

**DERS:** Ana döngüye iş eklerken `gecen`/mola muhasebesini kontrol et.
Sessizce tur periyodunu uzatmak kolay.

### 🚫 REDDEDİLEN: "farklı tarayıcıdan (kişisel Chrome) bak"

Kullanıcı önerisi: takibi kişisel Chrome'dan, gömülü sahibinden
hesabıyla yap — "belki dikkat çekmez".

**Reddedildi, üç somut sebep:**

1. **Aynı IP.** Tarayıcı değiştirmek ağ kimliğini değiştirmez. PX'in en
   ağır bastığı sinyal IP.
2. **Eş zamanlı iki oturum daha anormal, daha az değil.** Gerçek kullanıcı
   sahibinden'i aynı anda iki tarayıcıdan gezmez. Aynı hesapsa korelasyon
   daha da güçlü.
3. **Teknik engel:** Chrome user-data-dir'i kilitler → kişisel profil
   kullanılacaksa Chrome kapalı olmalı. Kopyalanırsa giriş taşınmaz.

Ayrıca kişisel Chrome'da sahibinden dışı her şey var (diğer girişler,
geçmiş) — otomasyona bağlamanın kazancı yok.

**Not:** Bot zaten 21.09'dan beri girişli profille çalışıyor
(`LOGIN_PROFIL_KULLAN=1`, `brave_oto_profile_login`). Hesap eklemek yeni
bir adım değildi, mevcut durumdu.

**Profil kilidi aslında işimize yarıyor:** bot ve runner aynı profili
paylaştığı için ikisi aynı anda açılamaz. Runner başta botu arar
(`psutil`, cmdline'da `oto_bot.py`), çalışıyorsa **hiç başlamaz** (exit 2).
Bu, "tek IP'den eş zamanlı iki oturum" sorununu kökten çözüyor.

### Ayarlar

```python
TAKIP_AKTIF = 0        # bottaki kanca VARSAYILAN KAPALI (runner kullanılıyor)
                       # kod silinmedi, TAKIP_AKTIF=1 ile geri açılır
TUR_BASINA_TAVAN = 30  # runner: bir çalıştırmada en fazla kontrol
BEKLEME = 30-60sn      # istekler arası
```

⚠️ **75 ilanlık kohort + tavan 30 → bir kontrol günü 3 çalıştırmada
biter.** Ölçüm penceresi yayılır. Ama kuyruk aday/kontrol **dengeli**
çektiği için yayılma her iki kohorta eşit uygulanır → FARK bozulmaz.
Tek kayıp `omur_gun` hassasiyeti.

### Kullanım

```bash
python takip_runner.py                    # sırası gelenleri kontrol et
python takip_runner.py --kohort           # önce yeni kohort ekle
python takip_runner.py --kuru             # ağ yok, ne yapacağını göster
python takip_runner.py --db kopya.db      # prova (asıl DB'ye dokunmaz)
python takip_runner.py --tavan 80         # bir günü tek seferde bitir
```

**Bot çalışırken `--kuru` ve `--db` dışındakiler çalışmaz** (exit 2).

### Testler (hepsi geçti)

```
sayfa_durumu()      10/10  — kritik: "PX + 'ilan bulunamadı' tuzağı → block"
ölçüm mantığı        4/4   — AYRIK / ÇAKIŞIYOR / yetersiz örnek / block≠satış
kuyruk zamanlama    10/10  — gün 0/2/3/4/16, denge, tavan, 'gitti' tekrarı
tur periyodu         ✓     — takip molaya gömülü, 267 tur/gün korundu
runner bot kontrolü  ✓     — bot açıkken exit 2
```
