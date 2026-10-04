# ELENEN ŞIKLAR — Kanıtlı Deneme Raporu

> Bu dosya, PerimeterX mücadelesinde **neyi denedik, ne oldu** sorusunun
> kanıtlı cevabıdır. Gemini ile tartışırken bu tabloyu kullan.

---

## ÖZET: 3 ŞIK KESİN ELENDİ, 1 ŞIK KISMEN ÇALIŞIYOR

| # | Denenen Yöntem | Sonuç | Kanıt |
|---|----------------|-------|-------|
| 1 | **Patchright** (Chromium patch) | ❌ ELENDİ | PX gelmedi ama CF + login wall çıktı |
| 2 | **Camoufox** (Firefox anti-detect) | ⚠️ KISMEN | CF'yi otomatik geçiyor, PX gelmiyor — AMA vasıta kategorisi IP'ye bağlı |
| 3 | **Mobil site** (m.sahibinden.com) | ❌ ELENDİ | Vasıta kategorisi PX HARD BLOCK |
| 4 | **Proxy** (3 residential) | ✅ ÇALIŞIYOR | 43 ilan geldi, PX yok — AMA IP zamanla yanıyor |

---

## 1. PATCHRIGHT — ❌ ELENDİ

**Ne denendi:** Playwright fork'u, Chromium fingerprint yamaları.
**Sonuç:** PX ekranı gelmedi ama **Cloudflare + login wall** çıktı.
**Neden elendi:** PX'i çözmedi, sadece farklı bir duvara çarptı.

---

## 2. CAMOUFOX — ⚠️ KISMEN ÇALIŞIYOR

**Ne denendi:** Firefox tabanlı anti-detect browser (v152.0.4-beta.31).
**Sonuç:**
- ✅ **Cloudflare'i OTOMATİK geçiyor** (~13sn), webdriver=False
- ✅ **PX gelmiyor** (normal sayfalarda)
- ✅ **Kalıcı profil çalışıyor** — cf_clearance + _pxvid saklandı, 2. açılışta CF hiç çıkmadı
- ✅ **Login başarılı** — <sahibinden_kullanici>, acc_type=bireysel_uye
- ❌ **AMA vasıta kategorisi hâlâ PX** (ev IP'si damgalı)

**Kanıt (`_temiz_log.txt`):**
```
[anasayfa]  https://www.sahibinden.com/          >>> BOS
[otomobil]  https://www.sahibinden.com/otomobil  >>> PX_BLOCK  (Access denied)
[bilgisayar] https://www.sahibinden.com/bilgisayar >>> OK  (43 ilan)
```

**Neden kısmen:** Camoufox PX'i "çözmüyor" — sadece **temiz IP'de PX hiç gelmiyor.**
Ev IP'si vasıta kategorisinde damgalı olduğu için Camoufox tek başına yetmiyor.

---

## 3. MOBİL SİTE — ❌ ELENDİ

**Ne denendi:** m.sahibinden.com (mobil versiyon).
**Sonuç:** Vasıta kategorisi **PX HARD BLOCK**.

**Kanıt (`_mobil_log.txt`):**
```
m_anasayfa         CF_BEKLIYOR  ilan=60  px=False
m_otomobil         PX_BLOCK     ilan=0   px=True   'Access to this page has been denied'
m_vasita           PX_BLOCK     ilan=0   px=True   'Access to this page has been denied'
m_otomobil_arama   PX_BLOCK     ilan=0   px=True   'Access to this page has been denied'
```

**Neden elendi:** Mobil site de aynı PX korumasını kullanıyor. Çözüm değil.

---

## 4. PROXY — ✅ ÇALIŞIYOR (ama kırılgan)

**Ne denendi:** 3 residential proxy (<proxy_host>:18178/18179/18180).
**Sonuç:** **3 proxy de 43 ilan getirdi, PX YOK!**

**Kanıt (`_proxy_log.txt`):**
```
proxy1_18178  cikis IP: 131.222.222.72   >>> OK  (ilan=43)
proxy2_18179  cikis IP: 153.56.218.125   >>> OK  (ilan=43)
proxy3_18180  cikis IP: 153.56.219.186   >>> OK  (ilan=43)
```

**Kanıt (`_teshis_log.txt` — taze profil + düz URL):**
```
proxy1_18178  cikis: 131.222.222.72   >>> OK  ilan=41 px=False
proxy2_18179  cikis: 153.56.218.125   >>> OK  ilan=41 px=False
proxy3_18180  cikis: 153.56.219.186   >>> OK  ilan=41 px=False
```

**KIRILGANLIK — `_tam_tarama_log.txt`:**
```
[s.1] https://www.sahibinden.com/otomobil?page=1
[s.1] [PX_BLOCK]  title='Access to this page has been denied'
proxy1_18178 PX yedi -> 3 tur dinlenmeye alindi
proxy2_18179 PX yedi -> 3 tur dinlenmeye alindi
proxy3_18180 PX yedi -> 3 tur dinlenmeye alindi
[X] Bu turda hicbir proxy basarili olamadi - 60sn mola
```

**Neden kırılgan:** Proxy IP'leri **zamanla yanıyor**. Aynı proxy tekrar kullanılınca
düz URL bile PX/login wall'a düşüyor.

---

## 5. EK BULGULAR (kritik)

### 5.1 URL query string PX tetikliyor
**Kanıt (`_sayfalama_log.txt` + `_taze_log.txt`):**
| URL | Sonuç |
|-----|-------|
| `/otomobil` (düz) | ✅ Çalışıyor (taze profil + taze IP) |
| `/otomobil?page=2` | ❌ PX_BLOCK |
| `/otomobil/2` (path) | ❌ PX_BLOCK |
| `/otomobil?sorting=date_desc` | ❌ PX_BLOCK |
| `/otomobil?a=1` (zararsız!) | ❌ PX_BLOCK |

**Sonuç:** Herhangi bir query string veya path segmenti PX tetikliyor.
**Sayfalama = PX.** Sadece düz kategori URL'si çalışıyor.

### 5.2 Kategori kuralı
- **Vasıta DIŞI** (ikinci_el, cep_telefonu, bilgisayar, is_makineleri): ✅ 41-43 ilan
- **Vasıta** (otomobil, motosiklet, ticari, hasarli, marka alt.): ❌ PX HARD BLOCK

### 5.3 İki katmanlı koruma
- **Cloudflare Turnstile**: Kendiliğinden geçiyor (~5-46sn). **TIKLAMAK BOZUYOR.**
- **PerimeterX**: `Access to this page has been denied`

---

## 6. SONUÇ — NEREDE KALDIK

**Çalışan formül (kanıtlı):**
```
Camoufox + TAZE profil + TAZE proxy IP + DÜZ URL (query'siz)
→ 41-43 ilan, PX yok
```

**Kırılan nokta:**
```
Aynı proxy IP tekrar kullanılınca → IP yanıyor → PX/login wall
Query string eklenince → PX
```

**Çözülmemiş sorun:**
1. **Sayfalama** — query string PX tetikliyor, alternatif yok
2. **IP ömrü** — proxy IP'leri kaç istek sonra yanıyor, ölçülmedi
3. **Login wall** — düz URL bazen login istiyor (login profili var ama test edilmedi)

---

## 7. HENÜZ DENENMEYENLER (Gemini şıkları)

- **SeleniumBase UC mode** — denenmedi
- **curl_cffi** (TLS fingerprint) — denenmedi
- **Bezier curve** fare hareketi — denenmedi
- **Login profili + proxy** — test yazıldı (`_proxy_login_test.py`), log okunmadı
