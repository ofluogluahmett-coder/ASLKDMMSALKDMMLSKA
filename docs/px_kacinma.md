# PX'e denk gelmeme stratejisi

Bu dosya PX'i **cozmekle** ilgili degil (o is `px_gec.py`). Burada tek soru
var: **PX ekranini hic gormemek icin ne yapilir?**

Temel kabul: PX bir duvar degil, bir **skor**. Skoru yukselten sey istek
*hacmi* degil, **anormallik**. O yuzden "daha yavas tara" tek basina cozum
degil — anormal davranisi yavas yapmak da yakalanir.

---

## Olculmus bulgular (tarih sirasina gore)

| Ne denendi | Sonuc | Kanit |
|---|---|---|
| Kalici profil (haftalarca bot) | **PX magneti** | PC botunda CF spam'i; anonim moda gecince bitti |
| Anonim profil (`user_data_dir` yok) | **Temiz** | `/otomobil` 10 tur, 252 ilan, 0 challenge |
| `?sorting=date_desc` | Temiz | ayni 10 tur |
| `&_=<ms>` cache-bust | Temiz | ayni 10 tur (jQuery gelenegi, sunucu yoksayiyor) |
| `&pagingSize=50` | **TEK ISTEKTE HARD BLOCK** | 05.10.2026: ilk istekte "Access to this page has been denied" |
| `&pagingSize=100` | Ayni | ayni test |
| Blok sonrasi taze profil | **Kurtarmadi** | yeni anonim oturum + duz URL yine bloklu → damga IP/oturum katmaninda |
| `pagingOffset=20`, `/otomobil/2` | **Olculemedi** | test IP'si zaten blokluydu, kontrol adimi da bloklu geldi → tekrar edilecek |
| Mobil site (`m.sahibinden.com`) vasita | Hard block | bkz. [elenen_siklar.md](elenen_siklar.md) |

**En onemli ders:** bir parametre ya tamamen zararsizdir ya da **tek istekte**
IP'yi yakar. Kademeli uyari yok. O yuzden yeni parametre denemesi asla botun
icinde, 10 turluk kosuda yapilmaz — elle, tek istekle, gozunun onunde yapilir.

---

## Kodda uygulanan 5 onlem (`oto_tarama.py`)

### 1. Parametre disiplini (en onemli)
`GUVENLI_PARAMETRELER = {"sorting", "_"}`. Bot acilista `ANA_URL`'in
parametrelerini bu listeye karsi dogrular; listede olmayan bir parametre
varsa **hic baslamaz** (`exit 2`), tarayici bile acilmaz. Ortak gelistirmede
birinin `pagingSize` ekleyip IP'yi yakmasini onler.
Bilincli gecmek icin: `PARAMETRE_KONTROL=0`.

### 2. Adaptif tempo
Turun TOPLAM suresi hedeflenir (`bekleme = max(3, hedef - gecen)`), hedef:

```
taban      = uniform(50, 75) sn
gece 02-07 = x2.5                 # ilan akisi durur, ayni tempo bedava risk
bos tur    = x1.35^n (n<=4)       # yeni ilan gelmiyorsa kategori sogumus
temkinli   = x2.0                 # challenge sonrasi 12 tur
tavan      = 420 sn
```

Mantik: **risk butcesini ilan akisinin oldugu yere harca.** Gece 03:00'te
dakikada bir istek atmak sifir getiri, tam risk.

### 3. Challenge devre kesici
Challenge gorulunce **cozulmeye calisilmaz**. Damgali oturumda atilan her
istek damgayi tazeler. Yapilan:

1. Oturum terk edilir (`driver.quit()`),
2. Katlanan mola: 5 dk → 15 dk → 30 dk → 60 dk (ust uste challenge sayisina gore),
3. Temiz gecici profille yeniden acilir,
4. 12 tur boyunca **temkinli** (periyot x2) gidilir, sonra normale doner.

### 4. Oturum tazeleme
`OTURUM_TAZELE_DK=90` — 90 dakikada bir driver kapatilip temiz profille
aciliyor (arada 20-40 sn bosluk). Anonim modda bu bedava: hicbir sey
kaybedilmiyor, `_px3`/`_pxvid` birikmesi sifirlaniyor. `0` = kapali.

### 5. Es zamanlilik kilidi
Ayni IP'den es zamanli iki oturum, tek oturumdan daha anormaldir.
`oto_tarama.py` kendi `oto_tarama.lock` dosyasini tutar ve `oto_bot.lock`
kilitliyse (yani `oto_bot.py` calisiyorsa) **baslamaz**.
Bilincli gecmek icin: `ESZAMANLI_IZIN=1`.

---

## Yapilmayanlar ve nedenleri

- **Yavaslatma tek basina cozum sayilmadi.** Olcum: `/otomobil` sayfa 1'i
  ~60 saniyede neredeyse tamamen yeniliyor (`ilan=21, yeni=20`). Yani
  periyodu uzatmak dogrudan **ilan kaciraktir**. Log'a bu durum icin uyari
  basiliyor: `[sayfa tam dondu: ilan kaciriyor olabilirsin]`.
- **Daha fazla fingerprint oyunu yok.** Aydin'in botu (rakip, ayni yontem)
  hicbir anti-detection kullanmiyor ve PX yemiyor → sorun imza degil,
  oturum/parametre hijyeni.
- **Proxy yok.** Olculdu, faydasi gosterilemedi; 3 sabit exit IP gercek
  rotasyon vermedi, ustune zorunlu restart'lar PX uretti.
  Bkz. [elenen_siklar.md](elenen_siklar.md).

## Siradaki olcumler (temiz IP ile, tek istek)

1. `pagingOffset=20` zararli mi? — **oto_bot.py her turda bunu kullaniyor**,
   PX'i surekli yemesinin sebebi bu olabilir. Oncelikli test.
2. `/otomobil/2` (path sayfalama) zararli mi?
3. `GORSEL_BLOK=0` (gorselleri gercekten indirmek) PX skorunu dusurur mu?
   "Hic gorsel istemeyen istemci" imzasi anormal gorunuyor olabilir.

Test kurali: **tek istek, sonra 30 dk sessizlik.** Blok gelirse o parametre
yanmistir, listeye asla girmez.

---

## 05.10.2026 gecesi — blogun OMRU olculdu (kotu haber)

`pagingSize` denemesi IP'yi 19:40 civari yakti. **22:42'de tek istekle
kontrol: hala `PX_BLOCK`.** Ayni IP ayni gun 19:11-19:27 arasi 11 tur
sorunsuz donmustu.

**Sonuc: bu bir hiz limiti degil, kalici IP damgasi.** Saatlerle olculuyor.
Pratik anlami: tek bir yanlis parametre, o IP'yi yarim gun kaybettirir.
Bu yuzden parametre beyaz listesi (`oto_tarama.py`) bir konfor degil, zorunluluk.

### Olcum aleti: `arac_px_olcum.py`

Parametre denemeleri artik elle degil bu aletle yapilir:

```
py -3.12 arac_px_olcum.py                      # kontrol: duz URL
py -3.12 arac_px_olcum.py "&pagingOffset=20"   # parametre dene
py -3.12 arac_px_olcum.py --yol /masaustu-donanim
```

Alet TEK istek atar, temiz anonim profil kullanir, hukmu
(`TEMIZ`/`PX_BLOCK`/`CF`/`BOS`) `px_olcum.log`'a yazar ve **son olcumden
20 dk gecmeden calismayi reddeder**. Disiplin insanin hafizasina
birakilmaz, alete gomulur.

### oto_bot.py'de bulunan sey — muhtemel kok neden

`_sayfa_url(0)` ILK SAYFAYI BILE `&pagingOffset=0` ile istiyordu. Yani
oto_bot'un attigi **hicbir istek duz URL degildi** — oto_tarama'nin 10 tur
temiz dondugu URL bicimini hic kullanmiyor. `pagingSize`'in tek istekte
oldurdugunu gorduktene gore bu ciddi bir suphe.

**Yapilan iki degisiklik (davranisi bozmadan):**

1. `_sayfa_url(0)` artik **duz URL** donduruyor (parametre eklemiyor).
   offset=0 ile duz URL ayni sayfadir; ama temiz oldugu OLCULEN bicim duz URL.
2. `TEK_SAYFA=1` env'i eklendi → derinlik plani `[(0,1)]`'e iner: tur basina
   istek 2-4'ten 1'e duser ve bot yalnizca olculmus-temiz bicimi kullanir.
   Bedeli: yogun saatte 1. sayfa ~60 sn'de tamamen yenildigi icin ilan
   kacabilir. **PX surekli geliyorsa ilk denenecek ayar budur.**

`pagingOffset>0` zararli mi? IP bloklu oldugu icin HENUZ OLCULEMEDI. Temiz
IP'de ilk yapilacak test bu.

### Geriye kalan buyuk degisken: CIKIS IP'si

Client tarafinda yapilacaklar bitmek uzere. Olculenler:

- Ayni IP, ayni kod, ayni saat: duz URL temiz → `pagingSize` → IP yarim gun yanik.
- Taze profil yanmis IP'yi KURTARMIYOR.
- Aydin'in botu (rakip) hicbir anti-detection kullanmiyor ve PX yemiyor
  → fark imzada degil, oturum/parametre hijyeninde ve IP itibarinda.

Siradaki kesin test (bedava): **telefon hotspot'u** ile ayni tek istekli
kontrol. Donerse sorun cikis IP'sindedir ve calisan bir yol elde edilir.

---

## 05.10.2026 23:08 — DAMGA KATEGORI BAZLI (kritik bulgu)

Ayni IP, ayni kod, ayni anonim profil, 25 dk arayla, tek istek:

| Saat | Kategori | Sonuc |
|---|---|---|
| 22:43 | `/otomobil` | **PX_BLOCK** ("Access to this page has been denied"), 15.6s |
| 23:08 | `/masaustu-donanim` | **21 ilan**, normal sayfa, 4.1s |

**Sonuc: IP tamamen yanik DEGIL.** sahibinden bu IP'yi genel olarak kabul
ediyor; reddettigi sey IP+**vasita kategorisi** kombinasyonu. Damga kategori
kapsamli.

Bu, PC bilesenleri botunun (apex_predator) neden hic bu derdi yasamadigini
da aciklar: o `masaustu-donanim` tariyor, vasitanin siki rejimine hic girmiyor.
"Oto botu block yiyor, PC botu yemiyor" farkinin kok nedeni kod degil,
KATEGORI.

### Bundan cikan calisma kurallari

1. Vasita kategorisinde risk butcesi cok daha kucuk. PC botunda zararsiz olan
   bir alisganlik (derin sayfalama, ekstra parametre, agresif tempo) burada
   IP'yi yariyor.
2. Vasita blogu, IP'nin geri kalan kullanimini etkilemiyor — yani blok
   sirasinda "sahibinden calisiyor mu?" diye kontrol etmek icin vasita-disi
   bir kategori kullanilabilir (teshis icin bedava kanal).
3. Blogun omru: 19:40'ta yandi, 22:43'te hala bloklu (3 saat +). Gun icinde
   kac saat sonra dondugu henuz olculmedi.
