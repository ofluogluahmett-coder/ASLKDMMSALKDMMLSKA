# PX GEC — DEVIR TESLIM NOTU (Gemini icin)

## GOREV
sahibinden.com botunda PerimeterX (px-captcha) ekranini gecmek.
Ayri bir yazilim: botu takip eder, PX penceresi gelince gecer.

## KULLANICI TARIFI (NET — dogrulandi)
PX ekraninda IKI kutu var, YAN YANA:
- **SOLDA**: kucuk kutu, icinde INSAN silueti
- **SAGDA**: buyuk uzun kutu ("basili tut"), bu kutu DOLUYOR

**SIRA**:
1. SOLDAKI kucuk insan kutusuna **TEK TIK**
2. **3-5 saniye bekle** (sagdaki buyuk kutu doluyor)
3. SAGDAKI buyuk kutuya **TIK**
-> PX gecilir, tek seferde.

## CANLI DOM BULGULARI (dogrulandi)
```
#px-captcha-wrapper  (dir=auto)
  .px-captcha-container   530x340 @(505,199)
    img.px-captcha-logo   188x85  @(676,199)
    .px-captcha-header    530x24  @(505,299)   "Baglantiniz kontrol ediliyor..."
    .px-captcha-message   530x37  @(505,348)   "Devam edebilmek icin lutfen asagidaki butona basili tutun."
    #px-captcha           530x95  @(505,400)   <-- BUTON ALANI (iki parcali)
      iframe (display:none, token=..., title="Insan dogrulama sinamasi")
    .px-captcha-refid     530x28  @(505,500)
```

## PENCERE / KOORDINAT
- Brave penceresi: hwnd=66686, baslik "Access to this page has been denied - Brave"
- Pencere MINIMIZE basliyor -> `ShowWindow(SW_RESTORE)` ile aciliyor
- client ekran kose = (7, 0), client boyut = 1539x874
- DOM: dpr=1.25, innerW=1540, innerH=738, screenX=2, screenY=8
- **SUPHELI NOKTA**: dpr=1.25 + innerW=1540 vs client 1539 -> tarayici ZOOM devrede.
  DOM koord -> ekran koord donusumu bu yuzden kayiyor. TIKLAMALAR ALAKASIZ YERE GIDIYOR.

## SORUN
`px_gec.py` PX'i buluyor, tikliyor ama **alakasiz yerlere** tikliyor.
Koordinat donusumu yanlis. Ekran goruntusunu GOZLE gormek gerekiyor.

## COZUM YOLU (GEMINI — gorsel analiz)
1. Ekran goruntusu al:
   ```
   cd oto_kelepir && python -c "from PIL import ImageGrab; ImageGrab.grab().save('_px_os_ekran.png')"
   ```
2. `_px_os_ekran.png` dosyasini OKU (gorsel analiz)
   -> PX kutusunu, insan ikonunu, buyuk kutuyu GOZLE bul
   -> GERCEK piksel koordinatlarini not et
3. `px_gec.py` icindeki `INSAN_OFFSET_X` / `BASILI_OFFSET_X` degerlerini
   gercek konuma gore ayarla (veya sabit koordinat yaz)
4. Test: `python px_gec.py --tek`

## DOSYALAR
- `px_gec.py` — ANA YAZILIM (tiklayici). `--tek` = tek sefer, parametresiz = surekli izle
- `_px_olc.py` — piksel olcum (dpr hipotez testi) — calistirilmadi
- `_px_yakala2.py` — PX gelince DOM dump eden yakalayici
- `_px_ekran4.py` — pencere + DOM koord + ekran goruntusu
- `_px_piksel.py` — piksel analizi (koyu blok tespiti)

## ORTAM
- Python 3.12, Pillow 12.3.0, pywin32 kurulu
- CDP port: 59964 (undetected-chromedriver)
- Brave profili: `brave_oto_profile_login`
- 3 residential proxy: <proxy_host> port 18178/18179/18180

## ONEMLI
- Kullanici TURKCE konusulmasini istiyor
- Kullanici onay istemiyor: "sana yap dedigimi yap, approve/save/run farketmeksizin yetki veriyorum"
- PX challenge UI AYNI bot penceresinde gorunuyor (kullanici dogruladi)
- `js.px-cloud.net` iframe'i BOS (sadece frameReady kopru scripti)
- `#px-captcha` icindeki iframe `display:none` ama div 530x95 GORUNUR/BOYUTLU
