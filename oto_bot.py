"""
OTO KELEPIR AVCISI — Faz 1 Toplayici (v3, veri hijyeni + fiyat dususu + olum takibi)

sahibinden.com/otomobil ilanlarini surekli tarar, oto_hafiza.db'ye yazar.
FAZ 1 = SADECE VERI TOPLAMA. Bildirim YOK. (Kelepir tespiti Faz 4.)

──────────── v3 EK OZELLIK ────────────
IS 1 — VERI HIJYENI:
  1a. Sifir km ayrimi (km<1000 → 'sifir', digeri → 'ikinci_el')
      Bucket'lar bu iki sinifta AYRI tutulur.
  1b. Motor/paket ayristirma:
      "1.6 TDI BlueMotion Comfortline" → motor_hacim=1.6, motor_tipi=tdi, paket=comfortline
      Bucket key yeni kolonlardan → gurultu kelimeleri (BMT/DSG vs.) elenir.
      3 kademeli bucket: L1=hacim+tip+paket, L2=hacim+tip, L3=temel.
  1c. Uc deger korumasi:
      Bucket'a fiyat ort'un %30-3x araligindaysa katilir. Absurt ilanlar
      fiyat_gecmisi'nde tutulur ama istatistige girmez.

IS 2 — FIYAT DUSUS SINYALI:
  Her ilan icin: ilk_fiyat, guncel_fiyat, toplam_dusus_yuzde, dusus_sayisi,
  son_dusus_tarih, ilan_yasi_gun. %5+ dususte 'FIYAT_DUSTU' isareti (bildirim yok).
  Bucket'a bakmaz — ilanin kendi gecmisiyle karsilasir. Yem taklit edemez.

IS 3 — ILAN OLUM TAKIBI:
  Ust uste 3 turda gorulmediyse durum='oldu'. Yasam < 7 gun → 'satildi_muhtemel'.
  Bucket'a medyan_yasam_gun (likidite) eklenir → hizli satilan bucket = gercekten ucuz.

──────────── ISKELET ────────────
apex_predator/sahibindennormal.py'den: WebDriverWait, cache-bust, tur periyodu
sabit, R2 self-heal, RotatingLog.

DB: SQLite (WAL). Sema geriye uyumlu — _ensure_column + _bucket_stat_migrate.
"""
import json
import logging
import os
import queue
import random
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv()

# ─── SABITLER ──────────────────────────────────────────────
ROOT          = Path(__file__).resolve().parent
DB_FILE       = ROOT / "oto_hafiza.db"
GORULMUS_FILE = ROOT / "oto_gorulmus.json"
PROFILE_DIR   = ROOT / "brave_oto_profile"
DUMP_DIR      = ROOT / "faz0_dump"
DUMP_MARKER   = DUMP_DIR / ".done"
LOG_FILE      = ROOT / "oto.log"

BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
_vm_raw = (os.getenv("BRAVE_VERSION_MAIN") or "").strip()
VERSION_MAIN = int(_vm_raw) if _vm_raw.isdigit() and int(_vm_raw) > 0 else None

OTO_URL_BASE = "https://www.sahibinden.com/otomobil?sorting=date_desc"

# ─── KADEMELI DERINLIK PLANI (17.09.2026 — apex_predator/sahibindennormal.py
# referansiyla kok neden analizi sonrasi) ─────────────────────────────────
# KOK NEDEN: PC bilesenleri botu block yemiyordu, oto botu yiyordu. Fark
# hiz DEGIL, DESEN: PC botu 5 FARKLI kategoriye (ANA_LINK her turda, ikincil
# linkler N-turda-bir), HER ZAMAN o kategorinin 1. sayfasina (pagingOffset
# hic kullanilmiyor) istek atiyordu. Oto botu ise TEK kategorinin (otomobil)
# offset=0,20,40,60,80 DERIN sayfalarini HER TURDA SIRAYLA, deterministik
# artan sekilde cekiyordu — "ayni sorgunun ardisik derin sayfalarini saniyeler
# icinde sirayla gezme" klasik scraper imzasidir, organik kullanici boyle
# davranmaz.
#
# COZUM: PC botunun ANA_LINK/IKINCIL_LINK N-turda-bir mantigini offset'lere
# uyarla — offset=0 HER turda (ana sayfa gibi), derin offsetler N-turda-bir
# (ikincil kategoriler gibi). Ayrica tur icindeki sira KARISTIRILIR —
# deterministik artan pattern kirilir.
#   (offset, N) → bu offset her N turda bir taranir (N=1 → her turda)
SAYFA_DERINLIK_PLANI = [
    (0,  1),   # pagingOffset=0  → HER turda (PC botunun ANA_LINK'i gibi)
    (20, 1),   # pagingOffset=20 → HER turda (22.09: yogun saatte sayfa 1
               # 4 dk'da yenileniyor, tur periyodu 3-5 dk → sayfa 1 tek
               # basina basabas kaliyordu; 2. sayfa payi acar)
    (40, 3),   # pagingOffset=40 → her 3 turda bir
    (60, 4),   # pagingOffset=60 → her 4 turda bir (22.09: KAPASITE DAR icin)
]
# Eski MAX_SAYFA hala .env icin okunuyor (geriye uyumluluk / log icin) ama
# tarama artik SAYFA_DERINLIK_PLANI'na gore yapiliyor, sabit range degil.
MAX_SAYFA    = int(os.getenv("MAX_SAYFA", str(len(SAYFA_DERINLIK_PLANI))))

# 05.10.2026 — TEK_SAYFA=1: derinlik plani SADECE 1. sayfaya iner (duz URL,
# hic pagingOffset yok). Tur basina istek 2-4'ten 1'e duser ve bot yalnizca
# olculmus-temiz URL bicimini kullanir. Bedeli: yogun saatte 1. sayfa
# ~60 sn'de tamamen yenilendigi icin ilan kacabilir. PX surekli geliyorsa
# ilk denenecek ayar budur.
if os.getenv("TEK_SAYFA", "0") == "1":
    SAYFA_DERINLIK_PLANI = [(0, 1)]

# 05.10.2026 — ANONIM_MOD=1: Brave'e --user-data-dir HIC verilmez; uc her
# acilista TEMIZ gecici profil yaratir. PC bilesenleri botunda CF "basili tut"
# spam'inin cozumu tam buydu (17.09.2026): haftalarca kullanilan kalici profil
# _px3/_pxvid cerezleriyle "bilinen bot" damgasi yiyordu. Yeni oto_tarama.py de
# bu modda /otomobil'de 11 tur boyunca TEK challenge gormedi.
# BEDELI: giris yapilmis oturum ve cf_clearance restart'lar arasinda TASINMAZ.
# Kalici profil davranisi icin ANONIM_MOD=0 (varsayilan).
ANONIM_MOD = os.getenv("ANONIM_MOD", "0") == "1"

RENDER_BEKLEME_TAVANI = 10
RENDER_SETTLE_MIN     = 2.5
RENDER_SETTLE_MAX     = 4.0
# Sayfalar arasi
SAYFA_ARASI_MIN       = 8.0
SAYFA_ARASI_MAX       = 16.0

# Tur periyodu
# 21.09.2026 — KULLANICI KARARI: PX/CF'yi artik ELLE gececek. Bu yuzden
# periyodu uzatmanin (davranissal gizlenme) anlami kalmadi — bot daha
# sik tarama yapsin diye ESKI degerlere (180-300sn) dusuruldu.
# (Onceki deneme: 360-480sn — PX'i geciktirmedi, sadece taramayi yavaslatti.)
TUR_PERIYOT_MIN    = 180
TUR_PERIYOT_MAX    = 300
TUR_TABAN_BEKLEME  = 90

# 18.09.2026 — INSAN SIMULASYONU (scroll/mouse) korunuyor — CF'nin JS
# tabanli sinyalinden bagimsiz olarak zararsiz. Ama SÜRE uzatma kismi
# (18.09 aksami TUR_PERIYOT/SAYFA_ARASI genisletmesi + "uzun okuma" eki)
# 18.09 gece GERI ALINDI — kontrol grubu testi CF'nin otomasyon davranisindan
# degil IP/oturum itibarindan kaynaklandigini gosterdi, yavaslatmanin
# faydasi kanitlanamadi. PC zaten acikken bosa saatlerce beklemenin
# anlami yok — block olursa kademeli mola (asagida) zaten devreye giriyor,
# uzun surerse kullanici manuel mudahale eder.
OKUMA_BEKLEME_MIN     = 2.0   # sayfa acilinca "goz gezdirme" bekleme
OKUMA_BEKLEME_MAX     = 4.0
SCROLL_SAYISI_MIN     = 1
SCROLL_SAYISI_MAX     = 3
SCROLL_BEKLEME_MIN    = 0.5   # scroll'lar arasi bekleme
SCROLL_BEKLEME_MAX    = 1.5
MOUSE_HAREKET_ORAN    = 0.4   # ARTIK KULLANILMIYOR — bkz. _mikro_mouse_hareketleri (20.09)
MIKRO_MOUSE_MIN       = 1     # her scroll civarinda ARA NOKTALI kac hareket
MIKRO_MOUSE_MAX       = 3

# Uzun okuma turu KALDIRILDI (18.09 gece) — standart tarama hizina donus.
UZUN_OKUMA_ORAN    = 0.0
UZUN_OKUMA_EK_MIN  = 0
UZUN_OKUMA_EK_MAX  = 0

# Ilk acilista CF gecilince hemen taramaya baslama — "insan sayfayi
# inceliyor" izlenimi icin ek bekleme. Tek seferlik (bot acilisinda),
# devam eden tarama hizini etkilemiyor — korundu.
CF_SONRASI_INCELEME_MIN = 30
CF_SONRASI_INCELEME_MAX = 60

UZUN_MOLA_ORAN     = 0.08
UZUN_MOLA_MIN      = 240
UZUN_MOLA_MAX      = 540

OLU_SURUCU_BEKLEME  = 60
# 22.09.2026 — chromedriver'a yapilan HTTP cagrilarinin okuma timeout'u.
# Selenium varsayilani 120sn; proxy asilinca bot o kadar bekliyordu.
DRIVER_CLIENT_TIMEOUT_SN = 35
# 23.09.2026 — sayfa yukleme tavani (driver.get icin). Bu sure asilirsa
# Selenium TimeoutException firlatir; sayfa kismen yuklenmis olabilir ve
# akis devam eder. Sonsuz bekleme yerine temiz bir sinir.
SAYFA_YUKLEME_TAVANI_SN = 30
# 23.09.2026 — CDP Page.navigate KAPALI (varsayilan).
# Olcum: 47 asilma olayinin 47'si CDP navigate yolunda, 0'i duz driver.get()
# yolunda gerceklesti. CDP navigate "fire-and-forget"tir: Selenium navigasyonu
# takip etmez, bu yuzden set_page_load_timeout UYGULANMAZ ve sonraki DOM
# sorgusu sayfa hic tamamlanmazsa sonsuza kadar bloklar.
# 1 yapilirsa eski davranisa (CDP + yukleme sirasinda erken sinyal) donulur.
PX_CDP_NAVIGATE = int(os.getenv("PX_CDP_NAVIGATE", "0") or "0")
GECICI_HATA_BEKLEME = 15
# Driver "ayakta" gorunup is yapamadigi durumda kac ust uste hatadan sonra
# zorla yeniden kurulacagi (20.09.2026 — islevsiz driver ile sonsuz dongu)
DRIVER_HATA_ESIK    = 3
# _duyarli_bekle: beklenen sureden bu kadar fazla gecerse makine uyumus
# sayilir ve loglanir (18.09 gecesi 30dk'lik mola ~11 saat surmustu)
UYKU_SAPMA_ESIK_SN  = 120

# Faz 1: is 2 esikleri
DUSUS_SINYAL_ESIK_YUZDE = 5.0    # %5+ dusus → 'FIYAT_DUSTU' isareti

# Faz 1: is 3 esikleri
OLUM_TUR_ESIK        = 3         # ust uste 3 tur gorulmezse 'oldu'
OLUM_HIZLI_GUN_ESIK  = 7         # < 7 gun → satildi_muhtemel; digeri → belirsiz

# Faz 1: is 1c uc deger korumasi
UC_DEGER_ALT_ORAN = 0.3          # kaba_ort * 0.3 alt sınırı
UC_DEGER_UST_KAT  = 3.0          # kaba_ort * 3 üst sınırı

# Olum takibi rafta — Faz 3'te detay-sayfa dogrulamasi ile geri gelir.
# medyan_yasam_gun sorgusunun tur basina ~300 gereksiz calismasi bloklanir.
OLUM_TAKIBI_AKTIF = False

# 17.09.2026 — DETAY WORKER GECICI KAPALI. Ana tarama (kademeli offset plani)
# kismen iyilesti ama detay worker (fetch → sonra driver.get() navigasyonuna
# cevrildi) hala sistematik block yiyor — IP itibar sorunu yontem farkindan
# bagimsiz gorunuyor. Once ana taramanin uzun sureli stabilitesini kanitlayip
# IP daha da sogusun, sonra detay worker'a donulecek. Kod SILINMEDI, sadece
# devre disi — worker thread hic baslamiyor, aday_uret cagrilmiyor.
DETAY_WORKER_AKTIF = False

# ─── FAZ 2.5: KOHORT TAKIBI (dogrulama dongusu) ────────────
# 24.09.2026 — "Botun kelepir dedigi ilanlar gercekten kelepir miydi?"
# En guvenilir olcut: birkac gun icinde SATILMIS olmalari.
#
# Her aday icin AYNI BUCKET'tan sapmasi ~0 olan bir ilan da takibe alinir
# (eslestirilmis kontrol). Olculen sey aday_gitme_orani'nin kendisi DEGIL,
# aday ile kontrol ARASINDAKI FARK — cunku rastgele ilan da satiliyor.
#
# HIZ: gecmiste detay worker yogun istekle block yemisti. Burada hacim
# kasten cok dusuk: her TAKIP_TUR_ARALIGI turda bir, 1 aday + 1 kontrol.
# ~5 dk'lik turda bu saatte ~4 istek eder. Sadece temiz turlarda calisir.
# 24.09.2026 — VARSAYILAN KAPALI. Takip artik BAGIMSIZ process'te:
# `python takip_runner.py`. Gerekce: bot 23-24.09 gecesi 01:44'te oldu ve
# 10.5 saat kapali kaldi; takip botun icinde olsaydi o sure boyunca o da
# olu olurdu ve kontrol gunleri (2/4/7/11/15) kayardi. Kohort olcumu botun
# ayakta olmasina bagli olamaz.
# Bottaki kanca SILINMEDI — TAKIP_AKTIF=1 ile geri acilabilir, ama ikisini
# birden acmak gereksiz (runner zaten ayni kuyrugu tuketir).
TAKIP_AKTIF        = int(os.getenv("TAKIP_AKTIF", "0") or "0")
TAKIP_TUR_ARALIGI  = int(os.getenv("TAKIP_TUR_ARALIGI", "3") or "3")
TAKIP_KOHORT_TUR   = int(os.getenv("TAKIP_KOHORT_TUR", "60") or "60")
TAKIP_KOHORT_TAVAN = int(os.getenv("TAKIP_KOHORT_TAVAN", "25") or "25")
# Takip molanin ICINDE calisir (bkz. ana dongu). Son takip isteginden sonra
# en az bu kadar sessiz kalinir — istekler tur basina yapismasin.
TAKIP_MOLA_TABANI  = int(os.getenv("TAKIP_MOLA_TABANI", "30") or "30")
_TAKIP_SEKME = {}

# ─── FAZ 3: DETAY sabitleri ────────────────────────────────
DETAY_KUYRUK_MAX     = 500       # queue.Queue maxsize
DETAY_FETCH_MIN      = 20.0      # istekler arasi rastgele bekleme (soft-block'tan kacinis)
DETAY_FETCH_MAX      = 40.0
DETAY_RETRY_MAX      = 2
DETAY_HTML_TIMEOUT   = 30
DETAY_MIN_BUCKET_N   = 8         # aday secimi: n>=8 bucket lazim
DETAY_ADAY_ESIK      = 0.20      # P1: medyanin %20 altinda
DETAY_ADAY_PER_TUR   = 4         # tur basina en fazla kuyruga eklenecek aday (dusuk)

# BLOCK DETECTION esikleri (ortak — hem detay hem ana tarama)
BLOCK_HTML_ESIK      = 50_000    # HTML byte'i bunun altindaysa block sayilir

# Detay worker backoff — sahibinden'in nefes almasi icin
DETAY_BLOCK_KISA_ESIK  = 3       # 3 ust uste block → kisa mola
DETAY_BLOCK_UZUN_ESIK  = 5       # 5 ust uste → uzun mola
DETAY_BLOCK_KISA_SN    = 1800    # 30 dk
DETAY_BLOCK_UZUN_SN    = 7200    # 2 saat

# 19.09.2026 — SAATLERCE BEKLEME KALDIRILDI. Kullanici talebi: CF/block
# geldiginde kendisi manuel mudahale edecek (challenge coz, captcha doldur
# vb.), bot bunu HIZLI fark edip devam etmeli — eskiden 30dk/2sa/6sa gibi
# kor (recheck yapmayan) molalar vardi, bu "test edip devam edecegim"
# akisiyla uyumsuzdu. Simdi: kisa, sabit moladan sonra tekrar dener;
# CF kontrolu de cok daha sik araliklarla title kontrol eder.
TARAMA_BLOCK_MOLA_SN   = [120, 180, 300]   # 2dk / 3dk / 5dk — artik saat degil
TARAMA_BOS_MOLA_SN     = 120                # 'bos' durumunda 2dk

# ══════════════════════════════════════════════════════════════════════
# 21.09.2026 — PERIMETERX (px-captcha) SAVUNMASI
# ══════════════════════════════════════════════════════════════════════
# Sahibinden Cloudflare'in YANINDA PerimeterX (px-cloud.net) kullaniyor.
# Duvar dump analizi (20260921_*_captcha.txt) gercek yapiyi ortaya koydu:
#
#   <meta name="description" content="px-captcha">
#   <div id="px-captcha-wrapper"> ... <div id="px-captcha">
#     <iframe token="..." title="İnsan doğrulama sınaması"></iframe>
#   <iframe src="https://js.px-cloud.net/?t=d-...&v=...&h=...&a=PXQerrWGjI">
#   window._pxUuid / window._pxMobile
#
# "Press & Hold" butonu IFRAME ICINDE ve iframe display:none — PX bunu
# JS ile acar. Botun iframe'e erisip basili-tut simulasyonu yapmasi gerekir.
#
# ONEMLI: PX skorlamasi sayfa YUKLENIRKEN (ilk 2-3 sn) yapilir. Sayfa
# geldikten sonra scroll yapmak PX icin gec kalir — bu yuzden
# _px_erken_sinyal() sayfa yuklenmeden ONCE ve YUKLENIRKEN calisir.
PX_OTOMATIK_COZ = (os.getenv("PX_OTOMATIK_COZ", "true") or "").strip().lower() in ("1", "true", "yes", "evet")
PX_MAX_DENEME   = int(os.getenv("PX_MAX_DENEME", "3") or "3")

# PX tespit isaretleri — title + DOM (meta/div/iframe). Title tek basina
# YETMEZ: bazi PX challenge'lari title'i degistirmez, sadece DOM'a enjekte eder.
PX_TITLE_ISARETLERI = (
    "access to this page has been denied",
    "px-captcha",
    "insan doğrulama",
    "insan dogrulama",
)

# 21.09.2026 — PX HARD BLOCK (IP karalistesi) — COZULEMEZ.
# SAHA BULGUSU (dump 20260921_150857_px_iframe_yok.txt): PX bazi durumlarda
# "Press & Hold" challenge'i HIC render etmez; bunun yerine title
# "Access to this page has been denied" olan, #px-captcha icinde SADECE
# px-loader (yukleniyor animasyonu) bulunan bir sayfa doner. captcha.js
# iframe'i ASLA olusturmaz. Bu, IP'nin PX tarafindan KARALISTEYE alindigi
# anlamina gelir — interaktif cozum MUMKUN DEGIL, tek caresi proxy degistirmek.
# Bu yuzden bu durum 'captcha' (cozulmeye calisilir) DEGIL, 'px_hard'
# (proxy failover tetikler) olarak siniflandirilir.
PX_HARD_TITLE_ISARETLERI = (
    "access to this page has been denied",
)
PX_DOM_ISARETLERI = (
    'content="px-captcha"',
    'id="px-captcha-wrapper"',
    'id="px-captcha"',
    "js.px-cloud.net",
    "px-captcha-container",
)

# "Press & Hold" simulasyonu — insan parmak/fare basisi 1.5-4 sn surer.
# Cok kisa (bot) veya cok uzun (takilma) supheli. Rastgele aralik.
PX_BASILI_TUT_MIN = 1.8
PX_BASILI_TUT_MAX = 3.6
# Basmadan once imleci butona goturme suresi (insan hedefe yaklasir)
PX_HEDEFLEME_MIN  = 0.4
PX_HEDEFLEME_MAX  = 1.1
# Cozum sonrasi sayfanin kendine gelmesi icin bekleme
PX_COZUM_SONRASI_BEKLE = 3.0

# ══════════════════════════════════════════════════════════════════════
# 21.09.2026 — FINGERPRINT TUTARLILIGI (PX'in 1 numarali tespit vektoru)
# ══════════════════════════════════════════════════════════════════════
# KOK NEDEN: PX sadece IP'ye bakmaz — TARAYICI PARMAK IZINE bakar. Bot
# su ana kadar UA/timezone/dil AYARLAMIYORDU; Brave varsayilanlari ile
# cikiyordu. Ama en kritik nokta SU: proxy'nin cikis IP'si (orn. TR
# residential) ile tarayicinin timezone/locale'i TUTARLI olmali. IP
# Istanbul ama navigator.language="en-US", timezone="UTC" ise PX bunu
# ANINDA "bot" olarak isaretler — bu, tek basina en guclu sinyaldir.
#
# Bu yuzden: TR cikis IP'si icin TR timezone (Europe/Istanbul) + tr-TR
# locale + Turkce Accept-Language zorluyoruz. UA'yi da Brave'in GERCEK
# UA'sindan turetiyoruz (sahte UA yazmak yerine — sahte UA'lar kendi
# icinde tutarsizlik uretir, orn. UA'da Chrome 153 yazarken navigator
# baska surum der).
PX_TIMEZONE   = (os.getenv("PX_TIMEZONE", "Europe/Istanbul") or "").strip()
PX_LOCALE     = (os.getenv("PX_LOCALE", "tr-TR") or "").strip()
PX_LANG       = (os.getenv("PX_LANG", "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7") or "").strip()
# Pencere boyutu — gercek kullanici ekranlarindan yaygin degerler. Sabit
# 1366x800 yerine hafif rastgelelik (her oturumda ayni boyut = parmak izi).
PX_PENCERE_SECENEKLERI = [
    (1366, 768), (1440, 900), (1536, 864), (1600, 900), (1920, 1080),
]
# navigator.webdriver / CDP sizinti yamalari. uc zaten cogunu yapar ama
# PX'in Runtime.enable tespitine karsi ek katman: webdriver'i sil, chrome
# runtime'i temizle, permissions sorgusunu duzelt.
PX_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
Object.defineProperty(navigator, 'language', {get: () => 'tr-TR'});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
// chrome runtime — headless/otomasyon izlerini temizle
if (window.chrome && window.chrome.runtime) {
    try { delete window.chrome.runtime.onConnect; } catch (e) {}
}
// Permissions.query — bildirim izni sorgusunda 'denied' yerine 'default'
const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (_origQuery) {
    window.navigator.permissions.query = (p) => (
        p && p.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : _origQuery(p)
    );
}
// WebGL vendor/renderer — sanal makine/headless imzasini gizle
try {
    const _getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';            // UNMASKED_VENDOR
        if (p === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER
        return _getParam.call(this, p);
    };
} catch (e) {}
"""
# Login/2FA duvarinda SESSIZ bekleme tavani. Bu sure boyunca HIC ISTEK
# ATILMAZ — sadece acik sayfa izlenir (bkz. _sayfa_duzeldi_mi). Kullanici
# giris yapinca saniyeler icinde devam edilir; hic gelmezse tur atlanir.
GIRIS_BEKLEME_SN       = 1800               # 30 dk (eski davranis — yedek)

# 21.09.2026 — KULLANICI TALEBI: "once giris yapmadan calismayi dene; login
# duvari cikinca proxy degistirip tekrar dene (belki bazi proxy'lerde login
# istemez)". Bu yuzden login duvarinda 30 dk bos beklemek yerine KISA bir
# manuel-cozum penceresi taninir; cozulmezse proxy degistirilir. Kullanici
# isterse bu pencere icinde elle giris yapip kurtarabilir.
GIRIS_MANUEL_PENCERE_SN = 90                # login duvari: manuel cozum penceresi

# 19.09.2026 — CF title isaretleri TEK KAYNAK. Eskiden bu liste kodda 4 ayri
# yere kopyalanmisti ve birbirinden kaymisti; en onemlisi Cloudflare'in
# TURKCE basligi ("Bir dakika lütfen...") HICBIRINDE yoktu — listede
# "bir saniye" yaziyordu. Sonuc: bot Turkce CF challenge'ini hic tanimiyordu,
# _cf_gec "gecildi" saniyor, _sayfa_durumu 'bos' diyordu. Sahada gozlenen
# basliklar (endpoint testi + kontrol grubu testi) buraya eklendi.
CF_TITLE_ISARETLERI = (
    "just a moment",        # CF (en)
    "bir dakika",           # CF (tr) — "Bir dakika lütfen..."  ← eksikti
    "bir saniye",
    "attention",            # "Attention Required!"
    "checking",             # "Checking your browser..."
    "cloudflare",
    "lütfen bekleyin",
    "lutfen bekleyin",
)


def _cf_title_mi(title):
    """title bir Cloudflare challenge sayfasina mi ait?"""
    t = (title or "").lower()
    return any(k in t for k in CF_TITLE_ISARETLERI)


def _px_title_mi(title):
    """23.09.2026 — SADECE title'a bakan PX kontrolu (DOM okumadan).

    `_px_mi` DOM imzalarina da bakar ama bunun icin page_source gerekir;
    tara_sayfa'daki erken kontrolde amac tam olarak AGIR cagrilardan
    kacinmak (bkz. WebDriverWait asilma bulgusu). Title ucuz bir komut.
    Title degismeyen PX challenge'lari bu kontrolden kacar — sorun degil,
    onlar asagidaki normal akista `_sayfa_durumu` ile yakalanir."""
    t = (title or "").lower()
    return any(k in t for k in PX_TITLE_ISARETLERI)


def _px_mi(html, title):
    """21.09.2026 — PerimeterX (px-captcha) tespiti. Title VEYA DOM.

    Title tek basina yetmez: PX bazi challenge'larda title'i degistirmez,
    sadece DOM'a enjekte eder. DOM isaretleri (meta px-captcha, div#px-captcha,
    js.px-cloud.net iframe) PX'in KENDI imzasi — gercek liste sayfasinda
    bulunmaz (duvar dump ile dogrulandi), yanlis pozitif riski yok."""
    t = (title or "").lower()
    if any(k in t for k in PX_TITLE_ISARETLERI):
        return True
    h = (html or "").lower()
    return any(k in h for k in PX_DOM_ISARETLERI)


def _px_hard_mi(html, title):
    """21.09.2026 — PX HARD BLOCK tespiti (IP karalistesi, COZULEMEZ).

    SAHA BULGUSU (dump 20260921_150857): PX bazen "Press & Hold" challenge'i
    HIC render etmez; title "Access to this page has been denied" olur ve
    #px-captcha icinde SADECE px-loader kalir — captcha.js iframe'i ASLA
    olusturmaz. Bu, IP'nin PX tarafindan karalisteye alindigi anlamina gelir.
    Interaktif cozum MUMKUN DEGIL; tek caresi proxy degistirmek.

    Bu durumu 'captcha'dan AYIRMAK KRITIK: 'captcha' dalinda proxy failover
    YOK (bilerek — cozulebilir challenge'da proxy degistirmek zararli), bu
    yuzden hard block 'captcha' sanilirsa bot ayni karalisteli IP'de sonsuz
    donguye girer (sahada gozlenen tam olarak buydu)."""
    t = (title or "").lower()
    return any(k in t for k in PX_HARD_TITLE_ISARETLERI)


CF_ILK_BEKLEME_SN      = 20       # CF tespitinde ilk kontrol oncesi bekleme
CF_TEKRAR_BEKLEME_SN   = 30       # sonraki kontroller arasi — sik kontrol,
                                   # kullanici elle cozunce hizlica yakalansin
CF_MAX_DENEME          = 5        # bu kadar basarisiz olursa dongu tekrar dener (uzun mola YOK)


# ─── LOG ───────────────────────────────────────────────────
class _AnindaFlushHandler(logging.Handler):
    """21.09.2026 — CANLI LOG. Saha gozlemi: RotatingFileHandler varsayilan
    olarak BLOK tamponlar; bot calisirken oto.log dakikalarca eski satirda
    takili kaliyordu (16:50:21'de dondu, oysa tur coktan baslamisti). Bu,
    canli teshisi imkansiz kiliyordu. Bu handler her kaydi ANINDA diske
    yazar (emit sonrasi flush) — boylece 'tail -f' gibi canli izlenebilir."""
    def __init__(self, filename, maxBytes, backupCount, encoding):
        super().__init__()
        self._fh = RotatingFileHandler(filename, maxBytes=maxBytes,
                                       backupCount=backupCount, encoding=encoding)
    def emit(self, record):
        try:
            msg = self.format(record)
            self._fh.stream.write(msg + self._fh.terminator)
            self._fh.flush()
            if self._fh.stream.tell() >= self._fh.maxBytes:
                self._fh.doRollover()
        except Exception:
            self.handleError(record)
    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass
        super().close()


def _log_kur():
    lg = logging.getLogger("oto")
    if lg.handlers:
        return lg
    lg.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [OTO] %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        fh = _AnindaFlushHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    except Exception:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    lg.addHandler(sh)
    return lg


log = _log_kur()


def _jitter(low, high):
    return random.uniform(low, high)


def _sayfa_url(offset):
    """offset: pagingOffset degeri (0, 20, 40, ...) — dogrudan, sayfa indeksi degil.

    05.10.2026 — OFFSET=0 ARTIK PARAMETRE EKLEMIYOR (duz URL).
    OLCUM: tanidik olmayan sayfalama parametresi (pagingSize=50) TEK ISTEKTE
    IP'yi yakti ("Access to this page has been denied", 3 saat sonra hala
    bloklu). Duz URL (?sorting=date_desc) ise 10 tur boyunca temiz dondu.
    pagingOffset=0 ile duz URL AYNI sayfayi verir — ama temiz oldugu OLCULEN
    bicim duz URL. Gereksiz parametre = bedava risk, o yuzden atildi.
    pagingOffset>0'in zararli olup olmadigi HENUZ OLCULMEDI (bkz.
    docs/px_kacinma.md); zararli cikarsa TEK_SAYFA=1 ile derin sayfalar kapanir.
    """
    if not offset:
        return OTO_URL_BASE
    return f"{OTO_URL_BASE}&pagingOffset={offset}"


def _tur_offsetleri(tur):
    """SAYFA_DERINLIK_PLANI'na gore bu turda taranacak offsetleri dondur.
    offset=0 her turda, derin offsetler N-turda-bir. Sira KARISTIRILIR —
    deterministik artan (0,20,40,...) pattern kirilir (kok neden analizi)."""
    offsetler = [off for off, n_tur in SAYFA_DERINLIK_PLANI if tur % n_tur == 0]
    random.shuffle(offsetler)
    return offsetler


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ─── MODEL/PAKET PARSER (Is 1b) ────────────────────────────
# Motor tipleri — genisletildi (olcum sonrasi: TDCi, Ti-VCT, D-CVVT, Twinport,
# Hybrid gibi kacirmalar eklendi).
_MOTOR_TIPLERI = [
    # Yaygın
    "tdi", "tsi", "dci", "tce", "cdi", "hdi", "crdi", "bluehdi",
    "tfsi", "fsi", "ecoboost", "ecoblue",
    # Ford
    "tdci", "ti-vct", "tivct", "duratorq", "duratec",
    # Fiat/Alfa
    "multijet", "multiair", "jtdm", "jtd", "e-torq", "etorq", "fire", "ts",
    # PSA
    "puretech", "vti", "thp",
    # Hyundai/Kia
    "gdi", "mpi", "kappa", "d-cvvt", "dcvvt", "crdi",
    # Honda
    "i-vtec", "vtec",
    # Toyota
    "d-4d", "vvt-i", "vvti", "d-4",
    # Mitsubishi
    "mivec",
    # Opel/GM
    "ecotec", "cdti", "smartech", "twinport",
    # Volvo (B/D serisi)
    "b3", "b4", "b5", "b6", "d3", "d4", "d5", "d6",
    # BMW/Nissan power kodları
    "xei",
    # Genel/hibrit
    "hybrid",
    # Diğer
    "d-cdti", "d-cdt", "boxer",
]
# Paketler — genişletildi. Marka-özel paketler eklendi.
_PAKETLER = [
    # Kullanıcının orijinal listesi
    "comfortline", "highline", "trendline", "elegance",
    "titanium", "dynamic", "icon", "style", "sport", "premium",
    "executive", "ambiente", "zetec", "easy", "urban", "cross",
    "touch", "intens", "joy", "active", "allure", "feel",
    # Ekstra (rapor'dan tespit edilen eksikler)
    "distinctive", "ambition", "attraction", "impression", "inspiration",
    "exclusive", "lounge", "business", "cosmo", "enjoy", "essentia",
    "comfort", "elite", "prime", "acenta", "tekna", "visia",
    "avantgarde", "amg",
    "life", "expression", "extreme",
    "gt", "gts", "gti", "gtd", "gli",
    "s-line", "sline", "r-line", "rline", "m-sport", "msport",
    "st-line", "stline", "st",
    "pop", "hybrid", "trend", "connect",
    "montecarlo", "monte-carlo",
    # Turkce/kisaltilmis
    "cosmoline", "cosmo", "delight", "pulse", "sensation",
    # Motor code cinsinden paket (renk kotu)
    "midline",
]

_RE_HACIM     = re.compile(r"(\d\.\d{1,2})")
_RE_MOTOR_TIP = re.compile(r"\b(" + "|".join(_MOTOR_TIPLERI) + r")\b", re.IGNORECASE)
_RE_PAKET     = re.compile(r"\b(" + "|".join(_PAKETLER) + r")\b", re.IGNORECASE)

# Audi ETS: "35 TFSI", "40 TDI" gibi Audi'nin yeni power-code notasyonu.
# Bu gorulunce sayiyi "hacim" olarak sakla (25/30/35/40/45/50/55) — normal
# hacim araligi (1.0-3.0) ile carpismaz, ETS kendi tutarli kategori olusturur.
_RE_AUDI_ETS = re.compile(r"\b(20|25|30|35|40|45|50|55)\s+(tdi|tfsi|tsi)\b", re.IGNORECASE)


def parse_model_string(model_str):
    """'1.6 TDI BlueMotion Comfortline' → (1.6, 'tdi', 'comfortline').
    Audi ETS: 'A3 Sedan 35 TFSI' → (35.0, 'tfsi', '').
    Gürültü kelimeleri (BMT/BlueMotion/DSG/otomatik/quattro vs.) sözlük dışı
    olduğu için otomatik atlanır."""
    if not model_str:
        return None, "", ""
    s = model_str.lower()

    hacim = None
    tipi  = ""
    paket = ""

    # 1) Audi ETS pattern (once, cunku bu 35 gibi sayilari alir)
    m = _RE_AUDI_ETS.search(s)
    if m:
        try:
            hacim = float(m.group(1))
            tipi = m.group(2).lower()
        except ValueError:
            pass

    # 2) Klasik hacim (1.6, 2.0, 1.33) — Audi ETS yakalanmazsa
    if hacim is None:
        m = _RE_HACIM.search(s)
        if m:
            try:
                hacim = float(m.group(1))
            except ValueError:
                hacim = None

    # 3) Motor tipi (Audi ETS'de zaten dolu)
    if not tipi:
        m = _RE_MOTOR_TIP.search(s)
        if m:
            tipi = m.group(1).lower()

    # 4) Paket (Distinctive, Ambition vs)
    m = _RE_PAKET.search(s)
    if m:
        paket = m.group(1).lower()
    # normalize: r-line/rline benzer yaz
    if paket:
        paket = paket.replace("-", "")

    # 5) DEFAULT — motor_tipi bosluk kalirsa "nasp" (natural aspirated),
    #    paket bosluk kalirsa "standart". Boylece L1 bucket'i her ilana
    #    uygulanabilir hale gelir, veri parcalanmaz.
    if not tipi:
        tipi = "nasp"
    if not paket:
        paket = "standart"

    return hacim, tipi, paket


def arac_sinifi_hesapla(km):
    """Is 1a: km<1000 → 'sifir', digeri → 'ikinci_el'."""
    if km is None:
        return "ikinci_el"
    return "sifir" if km < 1000 else "ikinci_el"


# ─── KATEGORIZASYON NETLIGI (18.09.2026) ───────────────────
# 1a. motor_hacim_grup: analiz gosterdi ki motor_hacim NULL olan ~1822 ilanin
# HICBIRINDE parser hatasi yok (0/1822 model string'inde X.Y deseni var ama
# hacim NULL) — hepsi gercekten hacimsiz isimlendirme (BMW/Mercedes power-code:
# "320i","C200"; Tofas eski model: "SLX","Toros"). Bu kodlar YILA GORE FARKLI
# gercek hacme karsilik gelebilir (orn. BMW 320i 2015 oncesi/sonrasi farkli
# motor) — TAHMIN ETMEK YANLIS VERI RISKI tasir. Bunun yerine ayri, acikca
# isaretli bir grup: 'bilinmiyor'. Boylece bilinmeyenler kendi bucket'inda
# toplanir, bilinenlerle KARISMAZ (kullanicinin istedigi tam olarak bu).
def motor_hacim_grup_hesapla(motor_hacim):
    """motor_hacim REAL → TEXT grup anahtari. NULL asla — 'bilinmiyor' yerine gecer."""
    if motor_hacim is None:
        return "bilinmiyor"
    return f"{motor_hacim:.1f}"


# 1c. Kasa tipi ipucu — liste sayfasinda kasa alani YOK (detay worker kapali),
# ama bazi model string'lerinde ipucu var ("Sedan","Hatchback","SW" vb).
# Bulunamazsa '' (bos) — bu bir "bilinmiyor" degil, "model isminde belirtilmemis"
# demek (cogu arac icin kasa zaten serinin dogal formu, ayrim gerekmeyebilir).
_KASA_IPUCU_PATTERNLER = [
    ("sedan",     re.compile(r"\bsedan\b", re.IGNORECASE)),
    ("hb",        re.compile(r"\b(hatchback|hb)\b", re.IGNORECASE)),
    ("sw",        re.compile(r"\b(sw|station\s*wagon|combi|variant|kombi|karavan)\b", re.IGNORECASE)),
    ("coupe",     re.compile(r"\bcoupe\b", re.IGNORECASE)),
    ("sportback", re.compile(r"\bsportback\b", re.IGNORECASE)),
    ("cabrio",    re.compile(r"\b(cabrio|cabriolet|convertible)\b", re.IGNORECASE)),
]


def kasa_ipucu_cikar(model_str, seri_str=""):
    """Model VE seri string'inde kasa tipi ipucu ara (Passat Variant gibi
    bazi kasa bilgisi seri adinda gecer). Bulunamazsa ''."""
    metin = f"{model_str or ''} {seri_str or ''}"
    for etiket, rgx in _KASA_IPUCU_PATTERNLER:
        if rgx.search(metin):
            return etiket
    return ""


# ─── COP AYRIMI (Is 2) — fiyattan BAGIMSIZ ─────────────────
# KRITIK KURAL: Dusuk fiyat TEK BASINA cop isareti DEGIL. Gercek kelepir de
# dusuk fiyatli. Ayrim fiyatla degil, BASLIK + BAGLAM ile yapilir (fiyat
# hatasi kurali haric — o da MODEL BAZLI asiri sapmaya bakar, mutlak fiyata degil).
#
# Oncelik sirasi: alim_ilani > parca > hasarli (bir baslikta birden fazla
# esleserse ilk esleyen kural kazanir — cunku "alim ilani" once elenmeli,
# "parca" ilanini "hasarli" sanmak daha az kritik bir hata).
_COP_ALIM = re.compile(
    r"\b(alınır|alinir|alıyorum|aliyorum|aranıyor|araniyor|aranan|alım|alim)\b",
    re.IGNORECASE
)
_COP_PARCA = re.compile(
    r"\b(parça|parca|çıkma|cikma|hurda|pert|belge|motoru|kaputu|kapısı|kapisi|tamponu)\b",
    re.IGNORECASE
)
_COP_HASARLI = re.compile(
    r"\b(hasarlı|hasarli|kazalı|kazali|ağır\s*hasar|agir\s*hasar|"
    r"motor\s*arızalı|motor\s*arizali|çalışmıyor|calismiyor|"
    r"yürümez|yurumez|biçilmez|bicilmez)\b",
    re.IGNORECASE
)

# Fiyat hatasi esigi — seri medyaninin bu kat ustunde/altinda ise cop
FIYAT_HATASI_UST_KAT = 6.0
FIYAT_HATASI_ALT_ORAN = 1.0 / 6.0


def cop_tespit_baslik(baslik):
    """Baslik regex kontrolu — alim/parca/hasarli. Fiyat hatasi burada YOK
    (o medyan bilgisi gerektirir, ayri fonksiyonda — bkz. fiyat_hatasi_mi).
    Donus: (cop_mu, cop_sebep)."""
    if not baslik:
        return 0, None
    if _COP_ALIM.search(baslik):
        return 1, "alim_ilani"
    if _COP_PARCA.search(baslik):
        return 1, "parca"
    if _COP_HASARLI.search(baslik):
        return 1, "hasarli"
    return 0, None


def fiyat_hatasi_mi(fiyat, seri_medyan):
    """Fiyat, seri medyaninin 6 kati ustunde veya 1/6 altindaysa fiyat hatasi.
    seri_medyan yoksa (yeterli veri yok) kontrol yapilmaz → False."""
    if not fiyat or not seri_medyan or seri_medyan <= 0:
        return False
    return fiyat > seri_medyan * FIYAT_HATASI_UST_KAT or fiyat < seri_medyan * FIYAT_HATASI_ALT_ORAN


def _seri_medyan_al(con, marka, seri):
    """marka+seri icin (cop_mu=0 ilanlardan) medyan fiyat. Yeterli veri
    (>=5 ilan) yoksa None doner — kontrol atlanir, erken/az veri asiri
    sapma gibi gorunup yanlislikla cop damgasi yemesin."""
    fiyatlar = [r[0] for r in con.execute(
        "SELECT fiyat FROM ilan WHERE marka=? AND seri=? AND fiyat IS NOT NULL AND cop_mu=0",
        (marka, seri)
    ).fetchall()]
    if len(fiyatlar) < 5:
        return None
    fiyatlar.sort()
    n = len(fiyatlar)
    return fiyatlar[n // 2] if n % 2 else (fiyatlar[n // 2 - 1] + fiyatlar[n // 2]) / 2


# ─── BUCKET KATMAN KARARI (Is 1b) ───────────────────────────
# Net kural: L1 uygun (hacim_grup+tip+paket dolu, "bilinmiyor"/"nasp"/
# "standart" defaultlari da GECERLI sayilir) VE o L1 bucket'ta n>=8 → L1.
# Degilse L2 uygun VE n>=8 → L2. Degilse L3 — ama L3'te motor/paket ayrimi
# YOK, sapma hesabi guvenilmez → kategori_guveni='dusuk'.
DEGERLENDIRME_MIN_N = 8


def degerlendirme_katmani_hesapla(con, marka, seri, hacim_grup, tip, paket, kasa, yil, sinif):
    """Bir ilanin hangi katmanda (L1/L2/L3) guvenilir degerlendirilecegini
    ve güven seviyesini (yuksek/orta/dusuk) hesaplar. bucket_stat'taki
    GUNCEL ilan_sayisi'na bakar — bucket_stat REBUILD EDILMIS olmali."""
    if marka and seri and hacim_grup and tip and paket and yil and sinif:
        key_l1 = _bucket_key("L1", marka, seri, hacim_grup, tip, paket, yil, sinif, kasa)
        n1 = con.execute(
            "SELECT ilan_sayisi FROM bucket_stat WHERE bucket_key=?", (key_l1,)
        ).fetchone()
        if n1 and n1[0] >= DEGERLENDIRME_MIN_N:
            return "L1", "yuksek"

    if marka and seri and hacim_grup and tip and yil and sinif:
        key_l2 = _bucket_key("L2", marka, seri, hacim_grup, tip, None, yil, sinif)
        n2 = con.execute(
            "SELECT ilan_sayisi FROM bucket_stat WHERE bucket_key=?", (key_l2,)
        ).fetchone()
        if n2 and n2[0] >= DEGERLENDIRME_MIN_N:
            return "L2", "orta"

    return "L3", "dusuk"


# ─── DB SEMASI ─────────────────────────────────────────────
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS ilan (
    ilan_id       TEXT PRIMARY KEY,
    marka         TEXT,
    seri          TEXT,
    model         TEXT,       -- ham model string (parse edilmemis)
    yil           INTEGER,
    km            INTEGER,
    yakit         TEXT,
    vites         TEXT,
    renk          TEXT,
    fiyat         REAL,
    il            TEXT,
    ilce          TEXT,
    kimden        TEXT,
    satici_id     TEXT,
    satici_ad     TEXT,
    satici_url    TEXT,
    ilan_tarih    TEXT,
    baslik        TEXT,
    url           TEXT,
    gorsel        TEXT,
    attr_ham      TEXT,
    ilk_gorulme   TEXT,
    son_gorulme   TEXT,
    son_fiyat     REAL,
    durum         TEXT        -- 'aktif' / 'oldu'
);

CREATE INDEX IF NOT EXISTS ix_ilan_marka_seri_yil ON ilan(marka, seri, yil);
CREATE INDEX IF NOT EXISTS ix_ilan_yil            ON ilan(yil);
CREATE INDEX IF NOT EXISTS ix_ilan_km             ON ilan(km);
CREATE INDEX IF NOT EXISTS ix_ilan_fiyat          ON ilan(fiyat);
CREATE INDEX IF NOT EXISTS ix_ilan_ilk_gorulme    ON ilan(ilk_gorulme);
CREATE INDEX IF NOT EXISTS ix_ilan_son_gorulme    ON ilan(son_gorulme);
CREATE INDEX IF NOT EXISTS ix_ilan_satici         ON ilan(satici_id);
CREATE INDEX IF NOT EXISTS ix_ilan_durum          ON ilan(durum);

CREATE TABLE IF NOT EXISTS fiyat_gecmisi (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ilan_id  TEXT NOT NULL,
    fiyat    REAL NOT NULL,
    tarih    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_gecmis_ilan ON fiyat_gecmisi(ilan_id);

-- ID reuse (sahibinden aynı ilan_id'yi tekrar kullaninca) → eski satir buraya
-- kopyalanir sonra ezilir. Bu KESIN olum sinyalidir: arac gercekten satildi/silindi.
-- Likidite olcumu icin en guvenilir kaynak.
CREATE TABLE IF NOT EXISTS ilan_arsiv (
    arsiv_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ilan_id      TEXT,
    marka        TEXT,
    seri         TEXT,
    model        TEXT,
    motor_hacim  REAL,
    motor_tipi   TEXT,
    paket        TEXT,
    arac_sinifi  TEXT,
    yil          INTEGER,
    km           INTEGER,
    fiyat        REAL,
    il           TEXT,
    ilce         TEXT,
    kimden       TEXT,
    satici_id    TEXT,
    satici_ad    TEXT,
    ilk_gorulme  TEXT,
    son_gorulme  TEXT,
    yasam_gun    REAL,
    arsiv_sebep  TEXT,     -- 'id_reuse' (ileride: 'satildi_dogrulandi' vs)
    arsiv_tarih  TEXT
);
CREATE INDEX IF NOT EXISTS ix_arsiv_ilan_id      ON ilan_arsiv(ilan_id);
CREATE INDEX IF NOT EXISTS ix_arsiv_marka_seri   ON ilan_arsiv(marka, seri);
CREATE INDEX IF NOT EXISTS ix_arsiv_msy          ON ilan_arsiv(marka, seri, yil);
CREATE INDEX IF NOT EXISTS ix_arsiv_sebep        ON ilan_arsiv(arsiv_sebep);

-- FAZ 3: detay sayfasi verileri (sadece aday ilanlar icin)
CREATE TABLE IF NOT EXISTS detay (
    ilan_id             TEXT PRIMARY KEY,
    -- boya/degisen
    orijinal_mi         INTEGER,      -- tam orijinal (other-pair)  1/0
    boyali              INTEGER,
    lokal_boyali        INTEGER,
    degisen             INTEGER,
    hasar_puani         REAL,
    -- yapilandirilmis alanlar
    agir_hasar          INTEGER,      -- 0/1 (Ağır Hasar Kayıtlı)
    plaka_uyruk         TEXT,
    yakit               TEXT,
    vites               TEXT,
    renk                TEXT,
    kasa                TEXT,
    motor_gucu          INTEGER,      -- hp
    motor_cc            INTEGER,      -- 1248 vs
    motor_hacim_kesin   REAL,         -- 1.2 (cc'den turetilmis)
    cekis               TEXT,
    takas               TEXT,
    servis_garantisi    TEXT,
    -- konum
    lat                 REAL,
    lon                 REAL,
    -- ilan kalitesi
    foto_sayisi         INTEGER,
    aciklama_uzunluk    INTEGER,
    aciklama_ozet       TEXT,         -- ilk 500 karakter
    ilan_kalite_skoru   REAL,
    -- bayraklar
    kirmizi_bayrak      TEXT,         -- JSON liste
    -- satici bilgisi (Faz 4 yem tespiti)
    satici_yil          TEXT,         -- '17. YIL' vb.
    yetki_belge         TEXT,
    okundu_tarih        TEXT
);
CREATE INDEX IF NOT EXISTS ix_detay_hasar        ON detay(hasar_puani);
CREATE INDEX IF NOT EXISTS ix_detay_agir         ON detay(agir_hasar);
CREATE INDEX IF NOT EXISTS ix_detay_kalite       ON detay(ilan_kalite_skoru);
"""

# bucket_stat semasi — DROP + CREATE her restart (idempotent rebuild).
# kimden bucket key'de DEGIL — ayni arac galeri/bireysel diye ikiye bolunmesin.
# Bunun yerine bucket icinde 3 ayri agrega: tumu / bireysel / galerici.
# Bireysel ort = taban (alis), galerici ort = tavan (satis), fark = cikis marji.
BUCKET_STAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS bucket_stat (
    bucket_key       TEXT PRIMARY KEY,
    katman           TEXT,           -- 'L1' / 'L2' / 'L3'
    marka            TEXT,
    seri             TEXT,
    motor_hacim_grup TEXT,           -- L1, L2 dolu ('1.6' veya 'bilinmiyor'); L3 NULL
    motor_tipi       TEXT,           -- L1, L2 dolu (nasp default); L3 ''
    paket            TEXT,           -- L1 dolu (standart default); L2, L3 ''
    kasa             TEXT,           -- L1 dolu (sedan/hb/sw/... veya 'na'); L2, L3 ''
    yil              INTEGER,
    arac_sinifi      TEXT,           -- 'sifir' / 'ikinci_el'
    -- Agrega — tumu (bireysel + galerici birlikte)
    ort_fiyat        REAL,
    medyan_fiyat     REAL,
    min_fiyat        REAL,
    max_fiyat        REAL,
    ilan_sayisi      INTEGER,        -- uc deger korumali (bucket'a katilanlar)
    -- Agrega — sadece bireysel (kimden='sahibinden')
    ort_fiyat_bireysel   REAL,
    medyan_bireysel      REAL,
    n_bireysel           INTEGER,
    -- Agrega — sadece galerici (kimden='galeriden')
    ort_fiyat_galerici   REAL,
    medyan_galerici      REAL,
    n_galerici           INTEGER,
    -- Likidite (Faz 3-4)
    medyan_yasam_gun REAL,
    guncellendi      TEXT
);
CREATE INDEX IF NOT EXISTS ix_bucket_katman     ON bucket_stat(katman);
CREATE INDEX IF NOT EXISTS ix_bucket_marka_seri ON bucket_stat(marka, seri);
CREATE INDEX IF NOT EXISTS ix_bucket_msy        ON bucket_stat(marka, seri, yil);
CREATE INDEX IF NOT EXISTS ix_bucket_sinif      ON bucket_stat(arac_sinifi);
"""


def _ensure_column(con, table, col, sql_type):
    """Idempotent ALTER TABLE ADD COLUMN."""
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}")


# ─── PARSE (liste sayfasi) ─────────────────────────────────
_RE_YIL     = re.compile(r"^(19|20)\d{2}$")
_RE_ILAN_ID = re.compile(r"^\d{6,}$")


def _txt(el):
    return el.get_text(strip=True) if el else ""


def _km_int(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


def _ilan_gorsel_url(row):
    img = row.select_one("img")
    if not img:
        return ""
    for attr in ("data-src", "data-original", "data-lazy", "data-lazyload", "data-srcset"):
        v = (img.get(attr) or "").strip()
        if v.startswith("http"):
            return v.split(",")[0].split(" ")[0] if attr.endswith("srcset") else v
    srcset = (img.get("srcset") or "").strip()
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first.startswith("http"):
            return first
    src = (img.get("src") or "").strip()
    if src.startswith("http") and "data:image" not in src:
        return src
    return ""


def parse_ilanlar(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = [r for r in soup.select("tr.searchResultsItem")
            if _RE_ILAN_ID.match(str(r.get("data-id") or ""))]

    ilanlar = []
    for row in rows:
        ilan_id = row.get("data-id")

        title_a = row.select_one("a.classifiedTitle, .classifiedTitle")
        link_a  = title_a or row.select_one("a[href*='/ilan/']")
        baslik  = _txt(title_a) or (title_a.get("title", "") if title_a else "")
        href    = (link_a.get("href", "") if link_a else "") or ""
        link    = href if href.startswith("http") else f"https://www.sahibinden.com{href}"

        fiyat_txt = _txt(row.select_one(
            ".searchResultsPriceValue, td.searchResultsPriceValue div, "
            ".classified-price-container, .price"
        ))
        fiyat = float(re.sub(r"[^\d]", "", fiyat_txt)) if re.search(r"\d", fiyat_txt) else None

        tag_cells = [_txt(c) for c in row.select("td.searchResultsTagAttributeValue")]
        tag_cells = [c for c in tag_cells if c]
        marka = tag_cells[0] if len(tag_cells) >= 1 else ""
        seri  = tag_cells[1] if len(tag_cells) >= 2 else ""
        model = tag_cells[2] if len(tag_cells) >= 3 else ""

        attr_cells = [_txt(c) for c in row.select("td.searchResultsAttributeValue")]
        attr_cells = [c for c in attr_cells if c]
        yil_c = attr_cells[0] if len(attr_cells) >= 1 else ""
        km_c  = attr_cells[1] if len(attr_cells) >= 2 else ""
        yil = int(yil_c) if _RE_YIL.match(yil_c) else None
        km  = _km_int(km_c)

        loc_td = row.select_one("td.searchResultsLocationValue")
        il, ilce = "", ""
        if loc_td:
            parts = [p.strip() for p in loc_td.get_text(separator="\n").split("\n") if p.strip()]
            il   = parts[0] if len(parts) >= 1 else ""
            ilce = parts[1] if len(parts) >= 2 else ""

        store_a = row.select_one("a.store-icon")
        if store_a:
            kimden     = "galeriden"
            satici_url = store_a.get("href", "") or ""
            satici_ad  = store_a.get("title", "") or ""
            m = re.match(r"https?://([^./]+)\.sahibinden\.com", satici_url)
            satici_id  = m.group(1) if m else ""
        else:
            kimden     = "sahibinden"
            satici_url = satici_ad = satici_id = ""

        date_td = row.select_one("td.searchResultsDateValue")
        ilan_tarih = ""
        if date_td:
            date_parts = [p.strip() for p in date_td.get_text(separator="|").split("|") if p.strip()]
            ilan_tarih = " ".join(date_parts)

        gorsel = _ilan_gorsel_url(row)

        if not gorsel or fiyat is None or not marka:
            continue

        # Is 1a + 1b — turetilen alanlar
        motor_hacim, motor_tipi, paket = parse_model_string(model)
        arac_sinifi = arac_sinifi_hesapla(km)
        motor_hacim_grup = motor_hacim_grup_hesapla(motor_hacim)
        kasa_ipucu = kasa_ipucu_cikar(model, seri)
        # Cop tespiti — sadece baslik regex burada (fiyat_hatasi DB medyani
        # gerektirir, ilan_yaz icinde ayrica kontrol edilir)
        cop_mu, cop_sebep = cop_tespit_baslik(baslik)

        ilanlar.append({
            "ilan_id":    str(ilan_id),
            "baslik":     baslik,
            "fiyat":      fiyat,
            "marka":      marka,
            "seri":       seri,
            "model":      model,
            "motor_hacim": motor_hacim,
            "motor_hacim_grup": motor_hacim_grup,
            "motor_tipi":  motor_tipi,
            "paket":       paket,
            "kasa_ipucu":  kasa_ipucu,
            "arac_sinifi": arac_sinifi,
            "cop_mu":      cop_mu,
            "cop_sebep":   cop_sebep,
            "yil":        yil,
            "km":         km,
            "yakit":      "",
            "vites":      "",
            "il":         il,
            "ilce":       ilce,
            "kimden":     kimden,
            "satici_id":  satici_id,
            "satici_ad":  satici_ad,
            "satici_url": satici_url,
            "ilan_tarih": ilan_tarih,
            "attr_ham":   tag_cells + attr_cells,
            "url":        link,
            "gorsel":     gorsel,
            "_row":       row,
        })

    return ilanlar, soup


# ─── ILAN YAZ ──────────────────────────────────────────────
def ilan_yaz(con, ilan):
    """Yeni: INSERT. Mevcut: son_gorulme + fiyat dususu + geriye-donuk doldurma.
    Is 2 kolonlarini (ilk_fiyat, guncel_fiyat, toplam_dusus_yuzde, dusus_sayisi,
    son_dusus_tarih, ilan_yasi_gun) hesaplar/gunceller."""
    now = _now_iso()
    attr_json = json.dumps(ilan["attr_ham"], ensure_ascii=False)

    mevcut = con.execute(
        "SELECT fiyat, baslik, satici_id, ilan_tarih, model, ilk_fiyat, dusus_sayisi, ilk_gorulme "
        "FROM ilan WHERE ilan_id=?", (ilan["ilan_id"],)
    ).fetchone()

    yeni_fiyat = ilan["fiyat"]

    if mevcut is None:
        # Cop tespiti tamamlanir: baslik regex zaten parse_ilanlar'da yapildi,
        # burada fiyat_hatasi kontrolu eklenir (seri medyani DB'den lazim).
        cop_mu, cop_sebep = ilan.get("cop_mu", 0), ilan.get("cop_sebep")
        if not cop_mu:
            seri_medyan = _seri_medyan_al(con, ilan["marka"], ilan["seri"])
            if fiyat_hatasi_mi(ilan["fiyat"], seri_medyan):
                cop_mu, cop_sebep = 1, "fiyat_hatasi"

        # INSERT — Is 2 icin: ilk_fiyat = guncel_fiyat = fiyat
        con.execute("""
            INSERT INTO ilan (
                ilan_id, marka, seri, model,
                motor_hacim, motor_hacim_grup, motor_tipi, paket, kasa_ipucu, arac_sinifi,
                cop_mu, cop_sebep,
                yil, km, yakit, vites,
                fiyat, il, ilce, kimden, satici_id, satici_ad, satici_url,
                ilan_tarih, baslik, url, gorsel, attr_ham,
                ilk_gorulme, son_gorulme, son_fiyat, durum,
                ilk_fiyat, guncel_fiyat, toplam_dusus_yuzde, dusus_sayisi,
                ilan_yasi_gun, gorulmedi_sayac
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ilan["ilan_id"], ilan["marka"], ilan["seri"], ilan["model"],
            ilan["motor_hacim"], ilan["motor_hacim_grup"], ilan["motor_tipi"], ilan["paket"],
            ilan["kasa_ipucu"], ilan["arac_sinifi"],
            cop_mu, cop_sebep,
            ilan["yil"], ilan["km"], ilan["yakit"], ilan["vites"],
            ilan["fiyat"], ilan["il"], ilan["ilce"],
            ilan["kimden"], ilan["satici_id"], ilan["satici_ad"], ilan["satici_url"],
            ilan["ilan_tarih"], ilan["baslik"], ilan["url"], ilan["gorsel"], attr_json,
            now, now, ilan["fiyat"], "aktif",
            yeni_fiyat, yeni_fiyat, 0.0, 0,
            0.0, 0,
        ))
        if yeni_fiyat is not None:
            con.execute(
                "INSERT INTO fiyat_gecmisi (ilan_id, fiyat, tarih) VALUES (?,?,?)",
                (ilan["ilan_id"], yeni_fiyat, now)
            )
        return "yeni"

    eski_fiyat, eski_baslik, eski_satici, eski_tarih, eski_model, ilk_fiyat, dusus_say, ilk_g = mevcut

    # son_gorulme + gorulmedi_sayac sifirla + durum='aktif' (canlanma edge case)
    con.execute(
        "UPDATE ilan SET son_gorulme=?, durum='aktif', gorulmedi_sayac=0 WHERE ilan_id=?",
        (now, ilan["ilan_id"])
    )

    # Geriye-donuk eksik alan doldurma
    if (not eski_baslik) and ilan["baslik"]:
        con.execute("UPDATE ilan SET baslik=? WHERE ilan_id=?",
                    (ilan["baslik"], ilan["ilan_id"]))
    if (not eski_satici) and ilan["satici_id"]:
        con.execute(
            "UPDATE ilan SET satici_id=?, satici_ad=?, satici_url=?, kimden=? "
            "WHERE ilan_id=?",
            (ilan["satici_id"], ilan["satici_ad"], ilan["satici_url"],
             ilan["kimden"], ilan["ilan_id"])
        )
    if (not eski_tarih) and ilan["ilan_tarih"]:
        con.execute("UPDATE ilan SET ilan_tarih=? WHERE ilan_id=?",
                    (ilan["ilan_tarih"], ilan["ilan_id"]))
    if (not eski_model) and ilan["model"]:
        con.execute("UPDATE ilan SET model=? WHERE ilan_id=?",
                    (ilan["model"], ilan["ilan_id"]))

    # ilan_yasi_gun her turda guncelle
    yasi = _gun_farki(ilk_g, now)
    con.execute("UPDATE ilan SET ilan_yasi_gun=? WHERE ilan_id=?",
                (yasi, ilan["ilan_id"]))

    # Fiyat degisimi — sinyal analizi Faz 4'te. fiyat_gecmisi'ne INSERT
    # ediyoruz (ham veri korunur), sayac/log sinyali yok.
    donus = "tekrar"
    if yeni_fiyat is not None and eski_fiyat != yeni_fiyat:
        fiyat_farki = abs(yeni_fiyat - eski_fiyat) / eski_fiyat if eski_fiyat else 0
        con.execute("UPDATE ilan SET son_fiyat=?, guncel_fiyat=? WHERE ilan_id=?",
                    (yeni_fiyat, yeni_fiyat, ilan["ilan_id"]))
        con.execute(
            "INSERT INTO fiyat_gecmisi (ilan_id, fiyat, tarih) VALUES (?,?,?)",
            (ilan["ilan_id"], yeni_fiyat, now)
        )
        if fiyat_farki >= 0.5:
            # ID reuse: sahibinden ilan_id'yi yeni bir arac icin tekrar kullanmis.
            # Bu KESIN olum sinyalidir — eski arac gercekten satildi/silindi.
            # ONCE eski satiri arsive kopyala, SONRA yenisiyle degistir.
            _ilan_arsive(con, ilan["ilan_id"], "id_reuse", now)
            con.execute("""UPDATE ilan SET
                            marka=?, seri=?, model=?, yil=?, km=?,
                            motor_hacim=?, motor_tipi=?, paket=?, arac_sinifi=?,
                            baslik=?, url=?, gorsel=?,
                            ilk_fiyat=?, guncel_fiyat=?, son_fiyat=?,
                            ilk_gorulme=?, son_gorulme=?
                          WHERE ilan_id=?""",
                        (ilan["marka"], ilan["seri"], ilan["model"],
                         ilan["yil"], ilan["km"],
                         ilan["motor_hacim"], ilan["motor_tipi"], ilan["paket"],
                         ilan["arac_sinifi"], ilan["baslik"], ilan["url"], ilan["gorsel"],
                         yeni_fiyat, yeni_fiyat, yeni_fiyat,
                         now, now, ilan["ilan_id"]))
            log.info(f"  ↻ ID reuse {ilan['ilan_id']}: fiyat_farki=%{fiyat_farki*100:.0f} "
                     f"→ eski arsive, yeni ilan satirda")
        donus = "fiyat_degisti"
    return donus


def _ilan_arsive(con, ilan_id, sebep, arsiv_tarih):
    """Bir ilanin mevcut satirini ilan_arsiv'e kopyala. yasam_gun hesaplanir."""
    row = con.execute("""SELECT ilan_id, marka, seri, model, motor_hacim, motor_tipi,
                                paket, arac_sinifi, yil, km, fiyat, il, ilce,
                                kimden, satici_id, satici_ad, ilk_gorulme, son_gorulme
                         FROM ilan WHERE ilan_id=?""", (ilan_id,)).fetchone()
    if not row:
        return
    (iid, marka, seri, model, mhac, mtip, pkt, sinif, yil, km, fiyat, il, ilce,
     kimden, sid, sad, ilk_g, son_g) = row
    yasam = _gun_farki(ilk_g, son_g or arsiv_tarih) if ilk_g else None
    con.execute("""
        INSERT INTO ilan_arsiv (ilan_id, marka, seri, model, motor_hacim, motor_tipi,
                                paket, arac_sinifi, yil, km, fiyat, il, ilce,
                                kimden, satici_id, satici_ad,
                                ilk_gorulme, son_gorulme, yasam_gun,
                                arsiv_sebep, arsiv_tarih)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (iid, marka, seri, model, mhac, mtip, pkt, sinif, yil, km, fiyat, il, ilce,
          kimden, sid, sad, ilk_g, son_g, yasam, sebep, arsiv_tarih))


def _gun_farki(iso1, iso2):
    """Iki ISO tarih arasi gun farki (float)."""
    try:
        d1 = datetime.fromisoformat(iso1)
        d2 = datetime.fromisoformat(iso2) if isinstance(iso2, str) else iso2
        return (d2 - d1).total_seconds() / 86400.0
    except Exception:
        return 0.0


# ─── BUCKET (Is 1b + 1c + 3) ───────────────────────────────
def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _bucket_key(katman, marka, seri, hacim_grup, tip, paket, yil, sinif, kasa=""):
    """kimden bucket key'in parcasi DEGIL — ayni arac galeri/bireysel diye
    ikiye bolunmesin. kimden agrega icinde ayri tutulur.

    18.09.2026: hacim artik hacim_grup (TEXT, 'bilinmiyor' dahil her zaman
    dolu) — NULL/None asla bucket'i parcalamaz, 'bilinmiyor' kendi ayri
    bucket'inda toplanir. kasa (kasa_ipucu) key'e eklendi — sedan/hb/sw farkli
    fiyatlanir, bos ise 'na' (kasa belirtilmemis) kendi grubu olur."""
    m, s = _norm(marka), _norm(seri)
    h = hacim_grup or "bilinmiyor"
    t = tip or "na"
    p = paket or "na"
    k = kasa or "na"
    if katman == "L1":
        return f"L1:{m}|{s}|{h}|{t}|{p}|{k}|{yil}|{sinif}"
    if katman == "L2":
        return f"L2:{m}|{s}|{h}|{t}|{yil}|{sinif}"
    return f"L3:{m}|{s}|{yil}|{sinif}"


def _uc_deger_filtre(fiyatlar):
    """Kaba ortalamanin %30 alti veya 3x ustu olanlari at."""
    if not fiyatlar:
        return []
    if len(fiyatlar) < 3:
        return list(fiyatlar)
    kaba = sum(fiyatlar) / len(fiyatlar)
    if kaba <= 0:
        return list(fiyatlar)
    alt, ust = UC_DEGER_ALT_ORAN * kaba, UC_DEGER_UST_KAT * kaba
    return [f for f in fiyatlar if alt <= f <= ust]


def _agrega(fiyatlar):
    """Uc-deger filtrelenmis fiyatlardan (ort, medyan, min, max, n).
    Bos ise (None, None, None, None, 0)."""
    fiyatlar = _uc_deger_filtre(fiyatlar)
    if not fiyatlar:
        return None, None, None, None, 0
    fiyatlar.sort()
    n = len(fiyatlar)
    medyan = fiyatlar[n // 2] if n % 2 else (fiyatlar[n // 2 - 1] + fiyatlar[n // 2]) / 2
    ort = sum(fiyatlar) / n
    return round(ort, 0), round(medyan, 0), min(fiyatlar), max(fiyatlar), n


def _bucket_yaz(con, katman, marka, seri, hacim_grup, tip, paket, yil, sinif, kasa=""):
    """Tek bucket icin 3 ayri agrega hesapla (tumu / bireysel / galerici).
    Uc deger korumasi UCUNE DE ayri uygulanir.

    18.09.2026: hacim_grup TEXT (her zaman dolu, 'bilinmiyor' dahil) — artik
    'hacim yok ise bucket olusturma' erken cikisi YOK, cunku 'bilinmiyor' da
    gecerli bir grup (kullanicinin istedigi: ayri bucket, karismasin).
    cop_mu=0 FILTRESI HER SORGUDA — cop ilanlar agregaya hic girmez."""
    if katman == "L1":
        if not (marka and seri and hacim_grup and tip and paket and yil and sinif):
            return
        sel = ("marka=? AND seri=? AND motor_hacim_grup=? AND motor_tipi=? AND paket=? "
               "AND kasa_ipucu=? AND yil=? AND arac_sinifi=? AND cop_mu=0")
        prm = (marka, seri, hacim_grup, tip, paket, kasa or "", yil, sinif)
    elif katman == "L2":
        if not (marka and seri and hacim_grup and tip and yil and sinif):
            return
        sel = ("marka=? AND seri=? AND motor_hacim_grup=? AND motor_tipi=? "
               "AND yil=? AND arac_sinifi=? AND cop_mu=0")
        prm = (marka, seri, hacim_grup, tip, yil, sinif)
    else:  # L3
        if not (marka and seri and yil and sinif):
            return
        sel = "marka=? AND seri=? AND yil=? AND arac_sinifi=? AND cop_mu=0"
        prm = (marka, seri, yil, sinif)

    # Tum ilanlar
    all_prices = [r[0] for r in con.execute(
        f"SELECT fiyat FROM ilan WHERE fiyat IS NOT NULL AND {sel}", prm
    ).fetchall()]
    if not all_prices:
        return

    ort_t, med_t, min_t, max_t, n_t = _agrega(all_prices)

    # Sadece bireysel
    bir_prices = [r[0] for r in con.execute(
        f"SELECT fiyat FROM ilan WHERE fiyat IS NOT NULL AND kimden='sahibinden' AND {sel}",
        prm
    ).fetchall()]
    ort_b, med_b, _, _, n_b = _agrega(bir_prices)

    # Sadece galerici
    gal_prices = [r[0] for r in con.execute(
        f"SELECT fiyat FROM ilan WHERE fiyat IS NOT NULL AND kimden='galeriden' AND {sel}",
        prm
    ).fetchall()]
    ort_g, med_g, _, _, n_g = _agrega(gal_prices)

    # Likidite (Faz 3-4) — flag'le bloklu, olu sorgu calismaz
    medyan_yasam = None
    if OLUM_TAKIBI_AKTIF:
        yasamlar = [r[0] for r in con.execute(
            f"SELECT yasam_gun FROM ilan WHERE yasam_gun IS NOT NULL "
            f"AND olum_tipi='satildi_muhtemel' AND {sel}",
            prm
        ).fetchall()]
        if yasamlar:
            yasamlar.sort()
            m_ = len(yasamlar)
            medyan_yasam = yasamlar[m_ // 2] if m_ % 2 else (yasamlar[m_ // 2 - 1] + yasamlar[m_ // 2]) / 2

    key = _bucket_key(katman, marka, seri, hacim_grup, tip, paket, yil, sinif, kasa)
    con.execute("""
        INSERT INTO bucket_stat
            (bucket_key, katman, marka, seri, motor_hacim_grup, motor_tipi, paket, kasa, yil,
             arac_sinifi,
             ort_fiyat, medyan_fiyat, min_fiyat, max_fiyat, ilan_sayisi,
             ort_fiyat_bireysel, medyan_bireysel, n_bireysel,
             ort_fiyat_galerici, medyan_galerici, n_galerici,
             medyan_yasam_gun, guncellendi)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(bucket_key) DO UPDATE SET
            ort_fiyat          = excluded.ort_fiyat,
            medyan_fiyat       = excluded.medyan_fiyat,
            min_fiyat          = excluded.min_fiyat,
            max_fiyat          = excluded.max_fiyat,
            ilan_sayisi        = excluded.ilan_sayisi,
            ort_fiyat_bireysel = excluded.ort_fiyat_bireysel,
            medyan_bireysel    = excluded.medyan_bireysel,
            n_bireysel         = excluded.n_bireysel,
            ort_fiyat_galerici = excluded.ort_fiyat_galerici,
            medyan_galerici    = excluded.medyan_galerici,
            n_galerici         = excluded.n_galerici,
            medyan_yasam_gun   = excluded.medyan_yasam_gun,
            guncellendi        = excluded.guncellendi
    """, (key, katman, marka, seri, hacim_grup, tip, paket, kasa or "", yil, sinif,
          ort_t, med_t, min_t, max_t, n_t,
          ort_b, med_b, n_b,
          ort_g, med_g, n_g,
          medyan_yasam, _now_iso()))


def bucket_guncelle(con, ilan):
    """Bir ilan yazildiktan sonra 3 katmanin ilgili anahtarlarini yeniden hesapla.
    cop_mu=1 olan ilanlar icin cagrilmamali (caller kontrol eder)."""
    marka = ilan["marka"]; seri = ilan["seri"]
    hacim_grup = ilan.get("motor_hacim_grup") or motor_hacim_grup_hesapla(ilan.get("motor_hacim"))
    tip = ilan["motor_tipi"]; paket = ilan["paket"]
    yil = ilan["yil"]; sinif = ilan["arac_sinifi"]
    kasa = ilan.get("kasa_ipucu", "")

    _bucket_yaz(con, "L3", marka, seri, None, None, None, yil, sinif)
    _bucket_yaz(con, "L2", marka, seri, hacim_grup, tip, None,  yil, sinif)
    _bucket_yaz(con, "L1", marka, seri, hacim_grup, tip, paket, yil, sinif, kasa)


def _bucket_rebuild_all(con):
    """Tum ilanlardan L1+L2+L3 bucket_stat'i sifirdan hesapla. cop_mu=1
    olanlar DISTINCT sorgularina bile girmez (agrega zaten cop_mu=0 filtreli,
    ama anahtar uretimini de kirletmemek icin baslangicta eleniyor)."""
    con.execute("DELETE FROM bucket_stat")

    l3 = con.execute("""
        SELECT DISTINCT marka, seri, yil, arac_sinifi FROM ilan
        WHERE marka<>'' AND seri<>'' AND yil IS NOT NULL AND arac_sinifi<>'' AND cop_mu=0
    """).fetchall()
    for m, s, y, sn in l3:
        _bucket_yaz(con, "L3", m, s, None, None, None, y, sn)

    l2 = con.execute("""
        SELECT DISTINCT marka, seri, motor_hacim_grup, motor_tipi, yil, arac_sinifi FROM ilan
        WHERE marka<>'' AND seri<>'' AND motor_hacim_grup IS NOT NULL
          AND motor_tipi<>'' AND yil IS NOT NULL AND arac_sinifi<>'' AND cop_mu=0
    """).fetchall()
    for m, s, h, t, y, sn in l2:
        _bucket_yaz(con, "L2", m, s, h, t, None, y, sn)

    l1 = con.execute("""
        SELECT DISTINCT marka, seri, motor_hacim_grup, motor_tipi, paket, kasa_ipucu, yil, arac_sinifi FROM ilan
        WHERE marka<>'' AND seri<>'' AND motor_hacim_grup IS NOT NULL
          AND motor_tipi<>'' AND paket<>'' AND yil IS NOT NULL AND arac_sinifi<>'' AND cop_mu=0
    """).fetchall()
    for m, s, h, t, p, k, y, sn in l1:
        _bucket_yaz(con, "L1", m, s, h, t, p, y, sn, k)

    con.commit()
    log.info(f"Bucket rebuild: L3={len(l3)}  L2={len(l2)}  L1={len(l1)}  "
             f"toplam={con.execute('SELECT COUNT(*) FROM bucket_stat').fetchone()[0]}")


# ─── ILAN OLUM TAKIBI (Is 3) ───────────────────────────────
def olum_tespit(con, tur_gorulen_ids):
    """Her tur sonu: gorulmeyen aktifleri sayac +1, sayaç>=3 → 'oldu'."""
    if not tur_gorulen_ids:
        return 0
    now = _now_iso()
    placeholders = ",".join("?" * len(tur_gorulen_ids))
    # Gorulmeyen aktiflerin sayacini +1
    con.execute(
        f"UPDATE ilan SET gorulmedi_sayac = COALESCE(gorulmedi_sayac,0) + 1 "
        f"WHERE durum='aktif' AND ilan_id NOT IN ({placeholders})",
        list(tur_gorulen_ids)
    )
    # 3 tur ust uste gorulmeyenler → 'oldu'
    olen = con.execute(
        "SELECT ilan_id, ilk_gorulme FROM ilan "
        "WHERE durum='aktif' AND gorulmedi_sayac >= ?",
        (OLUM_TUR_ESIK,)
    ).fetchall()
    sayi = 0
    for iid, ilk_g in olen:
        yasam = _gun_farki(ilk_g, now)
        tipi  = "satildi_muhtemel" if yasam < OLUM_HIZLI_GUN_ESIK else "belirsiz"
        con.execute(
            "UPDATE ilan SET durum='oldu', olum_tarih=?, yasam_gun=?, olum_tipi=? "
            "WHERE ilan_id=?",
            (now, yasam, tipi, iid)
        )
        sayi += 1
    con.commit()
    return sayi


# ─── BACKFILL (mevcut ilanlar icin geriye-donuk meta doldurma) ─
def _ilan_meta_backfill(con):
    """Mevcut ilanlarda arac_sinifi, motor_hacim, motor_tipi, paket, ilk_fiyat,
    guncel_fiyat, ilan_yasi_gun'u doldur. Idempotent."""
    now = _now_iso()
    rows = con.execute(
        "SELECT ilan_id, model, km, fiyat, ilk_gorulme, "
        "arac_sinifi, motor_hacim, motor_tipi, paket, ilk_fiyat, guncel_fiyat "
        "FROM ilan"
    ).fetchall()
    n_updated = 0
    for r in rows:
        (iid, model, km, fiyat, ilk_g,
         sinif_e, hacim_e, tip_e, paket_e, ilkf_e, guncelf_e) = r

        if sinif_e:  # daha once backfill yapilmis
            # Yalnizca ilan_yasi_gun'i tazele
            yasi = _gun_farki(ilk_g, now)
            con.execute("UPDATE ilan SET ilan_yasi_gun=? WHERE ilan_id=?",
                        (yasi, iid))
            continue

        hacim, tip, paket = parse_model_string(model or "")
        sinif = arac_sinifi_hesapla(km)
        yasi  = _gun_farki(ilk_g, now)

        # ilk_fiyat/guncel_fiyat: fiyat_gecmisi'nden turet (yoksa fiyat)
        gh = con.execute(
            "SELECT fiyat FROM fiyat_gecmisi WHERE ilan_id=? ORDER BY tarih",
            (iid,)
        ).fetchall()
        if gh:
            ilk_f = gh[0][0]
            son_f = gh[-1][0]
        else:
            ilk_f = son_f = fiyat
        toplam_dusus = 100.0 * (ilk_f - son_f) / ilk_f if ilk_f else 0.0

        con.execute("""
            UPDATE ilan SET
                motor_hacim = ?, motor_tipi = ?, paket = ?, arac_sinifi = ?,
                ilk_fiyat   = COALESCE(ilk_fiyat, ?),
                guncel_fiyat= COALESCE(guncel_fiyat, ?),
                toplam_dusus_yuzde = COALESCE(toplam_dusus_yuzde, ?),
                ilan_yasi_gun = ?
            WHERE ilan_id=?
        """, (hacim, tip, paket, sinif,
              ilk_f, son_f, toplam_dusus, yasi, iid))
        n_updated += 1

    con.commit()
    if n_updated:
        log.info(f"Backfill: {n_updated} ilana meta alani dolduruldu (arac_sinifi + motor/paket)")


# ─── DB INIT ───────────────────────────────────────────────
def db_init():
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)

    # Yeni kolonlari idempotent ekle (geriye uyumlu)
    _ilan_yeni_kolonlar = [
        ("arac_sinifi",       "TEXT"),
        ("motor_hacim",       "REAL"),
        ("motor_tipi",        "TEXT"),
        ("paket",             "TEXT"),
        ("ilk_fiyat",         "REAL"),
        ("guncel_fiyat",      "REAL"),
        ("toplam_dusus_yuzde","REAL DEFAULT 0"),
        ("dusus_sayisi",      "INTEGER DEFAULT 0"),
        ("son_dusus_tarih",   "TEXT"),
        ("ilan_yasi_gun",     "REAL"),
        ("olum_tarih",        "TEXT"),
        ("olum_tipi",         "TEXT"),
        ("yasam_gun",         "REAL"),
        ("gorulmedi_sayac",   "INTEGER DEFAULT 0"),
        ("satici_id",         "TEXT"),
        ("satici_ad",         "TEXT"),
        ("satici_url",        "TEXT"),
        ("ilan_tarih",        "TEXT"),
        ("renk",              "TEXT"),
        # 18.09.2026 — Kategorizasyon netligi + cop ayrimi
        ("motor_hacim_grup",     "TEXT"),   # motor_hacim doluysa "1.6", yoksa 'bilinmiyor' — bucket icin, NULL asla
        ("kasa_ipucu",           "TEXT"),   # model string'inden: sedan/hb/sw/coupe/sportback, yoksa ''
        ("degerlendirme_katmani","TEXT"),   # 'L1' / 'L2' / 'L3' — bu ilan hangi katmanda guvenilir degerlendirilir
        ("kategori_guveni",      "TEXT"),   # 'yuksek' / 'orta' / 'dusuk'
        ("cop_mu",               "INTEGER DEFAULT 0"),
        ("cop_sebep",            "TEXT"),   # 'alim_ilani' / 'parca' / 'hasarli' / 'fiyat_hatasi'
    ]
    for col, ctype in _ilan_yeni_kolonlar:
        _ensure_column(con, "ilan", col, ctype)

    # bucket_stat: DROP + CREATE her restart (idempotent rebuild)
    con.execute("DROP TABLE IF EXISTS bucket_stat")
    con.executescript(BUCKET_STAT_SCHEMA)
    con.execute("DROP TABLE IF EXISTS fiyat_istatistik")  # legacy

    # Backfill: mevcut ilanlarin meta alanlarini doldur
    ilan_n = con.execute("SELECT COUNT(*) FROM ilan").fetchone()[0]
    if ilan_n > 0:
        _ilan_meta_backfill(con)
        log.info(f"bucket_stat sifirdan rebuild ediliyor ({ilan_n} ilan)...")
        _bucket_rebuild_all(con)

    con.commit()
    return con


# ─── GORULMUS ──────────────────────────────────────────────
def goruldu_yukle():
    if not GORULMUS_FILE.exists():
        return set()
    try:
        return set(json.loads(GORULMUS_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def goruldu_kaydet(gorulmus):
    GORULMUS_FILE.write_text(
        json.dumps(sorted(gorulmus), ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


# ─── FAZ0 DUMP ─────────────────────────────────────────────
def faz0_dump_gerek():
    return not DUMP_MARKER.exists()


def faz0_dump_yap(html, ilanlar, soup):
    DUMP_DIR.mkdir(exist_ok=True)
    try:
        (DUMP_DIR / "_page_source.html").write_text(html, encoding="utf-8")
    except Exception:
        pass
    try:
        (DUMP_DIR / "_head.html").write_text(str(soup.head) if soup.head else "", encoding="utf-8")
    except Exception:
        pass
    for i, ilan in enumerate(ilanlar[:20], 1):
        try:
            (DUMP_DIR / f"ilan_{i:02d}_{ilan['ilan_id']}.html").write_text(
                str(ilan["_row"]), encoding="utf-8"
            )
        except Exception:
            pass
    DUMP_MARKER.write_text(_now_iso(), encoding="utf-8")
    log.info(f"[FAZ0] page_source + head + {min(20, len(ilanlar))} ilan → {DUMP_DIR.name}/")


# ─── DRIVER ────────────────────────────────────────────────
# 17.09.2026 — googlechromelabs.github.io adresine TLS/SNI seviyesinde agdan
# erisim engelli (curl.exe ile DOGRUDAN test edilip dogrulandi — oto_bot.py
# koduyla ilgisiz, makine/ag kaynakli). undetected_chromedriver'in patcher.py
# `auto()` metodu, driver_executable_path VERILMEDIGI surece HER CAGRIDA
# mevcut chromedriver'i siler + fetch_release_number() ile bu engelli adrese
# network istegi atar. Path ACIKCA verilirse (_custom_exe_path=True) kod
# SADECE lokal binary patch yapar, network'e HIC gitmez.
#
# Cozum: chromedriver.exe'yi storage.googleapis.com uzerinden (bu domain acik)
# manuel indirip proje klasorune koyduk, path'i acikca veriyoruz.
_CHROMEDRIVER_LOCAL = ROOT / "chromedriver.exe"


# 19.09.2026 — KALICI PROFIL GERI GELDI (17.09 karari tersine cevrildi).
#
# 17.09'daki karar: "kalici profil damgalaniyor" → user-data-dir kaldirildi,
# her acilista temiz/gecici profil. O an 3 tur temiz gectigi icin dogru
# sanildi. 18-19.09'daki kanitlar bunu curuttu:
#
#   1) PC 12 saat kapali kaldi (IP itibari sogudu) → yine ilk acilista
#      challenge. Yani "IP itibari" ya da "profil damgasi" degil.
#   2) Kullanici AYNI ANDA, AYNI IP'den, kendi gunluk Brave profiliyle
#      ayni kategoriyi sorunsuz aciyor.
#   3) Kontrol grubu testi (Selenium YOK, duz subprocess + CDP) de
#      challenge yedi — ama o test de tempfile.mkdtemp() ile BOS profil
#      kullaniyordu. Yani "otomasyon" degiskeni elendi, geriye tek ortak
#      degisken kaldi: PROFILIN BOS OLMASI.
#
# Sonuc: CF challenge'i bir kez gecen tarayiciya cf_clearance cerezi verir,
# sonraki isteklerde onu gorup sessizce gecirir. Her acilista profili cope
# atmak = her acilista sifir guvenle, cerezsiz basvurmak = her seferinde
# tam challenge. Kullanicinin gunluk profili bu cerezi zaten tasiyor,
# o yuzden hic challenge gormuyor.
#
# YENI DAVRANIS: sabit bir profil klasoru (brave_oto_profile_v2) kullanilir,
# restart'lar arasinda YASAR. Challenge bir kez elle cozulur, cf_clearance
# profilde kalir, sonraki acilislar sessiz gecmeli.
#
# NOT (damga riski): 17.09'daki gozlem tamamen yanlis olmayabilir — saatlerce
# agresif trafik tasiyan bir profil zamanla skor kaybedebilir. Fark su:
# eskiden profil TEK kurtulus yoluydu; simdi profil bozulursa klasoru silip
# yeniden challenge cozmek yeterli (asagidaki _BRAVE_PROFIL yolunu sil).
#
# 18.09.2026: cache-bust (?cb=) KALDIRILDI — normal kullanici bunu
# kullanmaz, deterministik bir bot imzasiydi (bkz. CLAUDE.md davranissal
# teshis).
# ══════════════════════════════════════════════════════════════════
# PROXY HAVUZU + FAILOVER (mekanik altyapi — liste oku, sirayla dene,
# block olursa sonrakine gec). Rotasyon degil failover: ayni anda tek
# proxy aktif, digerleri yedek. CF/block gelen proxy dinlendirilir.
# ══════════════════════════════════════════════════════════════════
_PROXY_RAW = (os.getenv("PROXY_LIST", "") or "").strip()
PROXY_HAVUZU = [p.strip() for p in _PROXY_RAW.split(",") if p.strip()]

# 21.09.2026 — ROTATING PROXY MODU (VARSAYILAN: KAPALI).
# Kullanicinin saglayicisi IP rotasyonu DESTEKLEMIYOR (teyit edildi:
# "elimdeki proxyler ip rotasyonu desteklemiyor maalesef"). Bu yuzden
# rotating modu VARSAYILAN OLARAK KAPALI tutuyoruz; bot 3 sabit proxy'li
# KARANTINA/FAILOVER mantigiyla calisir (bkz. _proxy_sec / _proxy_hard_bildir).
#
# Kod yolu ileride rotasyon destekli bir saglayiciya gecilirse diye
# KORUNUYOR: PROXY_ROTATING=1 ortam degiskeni ile acilabilir. Otomatik
# tespit (tek endpoint → rotating) KALDIRILDI — cunku tek proxy'li sabit
# kurulumda yanlislikla rotating'e gecmesin.
_rot_env = (os.getenv("PROXY_ROTATING", "") or "").strip().lower()
PROXY_ROTATING = _rot_env in ("1", "true", "evet", "yes")

# Rotating'de ust uste kac hard block sonrasi mola verilecek (sonsuz dongu
# korumasi). Her basarili sayfa yuklemesinde sayac sifirlanir.
ROTATING_MAX_DENEME = 5
# Rotating'de mola suresi (endpoint gercekten olu olabilir).
ROTATING_MOLA_SN = 60
# Rotating'de ust uste hard block sayaci (runtime).
_rotating_ardarda_hard = {"n": 0}

# Her proxy icin durum: {block_sayisi, dinlen_bitis (monotonic), hard_sayisi}
# 22.09.2026 — 'hard_sayisi' sayac olarak duruyor (log/tani icin) ama
# KARANTINA KALDIRILDI: uzun bekleme yerine 30sn'lik kisa dinlenme var
# (bkz. PROXY_HARD_KARANTINA_SN / _proxy_hard_bildir).
_proxy_durum = {
    px: {"block": 0, "dinlen_bitis": 0.0, "hard_sayisi": 0}
    for px in PROXY_HAVUZU
}
PROXY_DINLENME_SN = 30           # 22.09: karantina kaldirildi — sadece 30sn
PROXY_BLOCK_ESIK  = 2            # kac block sonra dinlendirilecek

# ══════════════════════════════════════════════════════════════════════
# 21.09.2026 — PROAKTIF PROXY ROTASYONU (kullanici stratejisi).
# ══════════════════════════════════════════════════════════════════════
# Kullanici karari: "proxylerimizi misal 3-4 turda bir cevirip aralarinda
# taze veri alarak fx'i ne kadar geciktirirsek bizim icin o kadar hos olur."
#
# MANTIK: PX hard block yemeyi BEKLEMEK yerine, proxy'yi PX yemeden ONCE
# kendiliginden degistir. Boylece:
#   - Her IP daha az tur gorur → PX davranissal skoru yavas yukselir.
#   - Karantina/hard block dongusune girmeden temiz IP'ler arasinda gezinilir.
#   - Arada "isinma turu" ile her yeni IP'ye taze oturum acilir.
#
# PROAKTIF_ROTASYON_TUR: kac turda bir proxy degistirilecek. 0 = KAPALI.
# 3 tur onerilir (kullanici "3-4 turda bir" dedi). Cok kucuk deger (1-2)
# her turda proxy degistirir → surekli CF challenge → verim dusebilir.
PROAKTIF_ROTASYON_TUR = int(os.getenv("PROAKTIF_ROTASYON_TUR", "3") or "3")
# Proaktif rotasyonda yeni proxy'ye gecince kac saniye "taze oturum" molasi
# verilecek (isinma turu bunun uzerine eklenir). Kisa tutulur — amac IP
# degistirmek, uzun beklemek degil.
PROAKTIF_ROTASYON_MOLA_SN = 20
# 21.09.2026 — KULLANICI KARARI: "proxy degisim islemini SADECE tarama
# araliklarinda (tur sonu) yap; CF/PX gelmeden once." Boylece tarama
# ORTASINDA proxy degistirip yari cozulmus challenge'i sifirlamayiz ve
# kullanici elle CF/PX gececegi icin reaktif degisime gerek kalmaz.
#   1 = proxy SADECE tur sonu araliginda degisir (onerilen)
#   0 = eski davranis (block/px_hard aninda reaktif degisim)
PROXY_DEGISIM_SADECE_ARADA = int(os.getenv("PROXY_DEGISIM_SADECE_ARADA", "1") or "1")
# 22.09.2026 — KULLANICI KARARI: karantina KALDIRILDI. Eski kademeli sistem
# (45dk / 2sa / 6sa) botu saatlerce bos bekletiyordu; 19.09'da tarama
# molalarinda ayni desen zaten reddedilmisti. Artik hard block'ta proxy
# sadece 30sn dinlenir, bot sirakine gecip taramaya devam eder. PX gelirse
# elle geciliyor.
PROXY_HARD_KARANTINA_SN = [30, 30, 30]


def _proxy_sec(haric=None):
    """Saglikli (dinlenme suresi gecmis) ilk proxy'yi dondur.

    21.09.2026 — KRITIK DUZELTME (sonsuz failover dongusu):
    ESKI davranis: hepsi dinlenmedeyse 'en erken bosalacak' proxy'yi YINE
    DE dondururdu. Caller bunu 'yeni proxy' sanip hemen `continue` ediyor,
    ama o proxy HALA KARANTINADA oldugu icin aninda tekrar hard block
    yiyordu → 18178→18179→18180→18178 sonsuz dongusu (sahada gozlendi:
    16:52'de uc proxy de hard block yedi ve bot saniyeler icinde donup
    durdu, hic beklemedi).

    YENI davranis: SADECE gercekten uygun (dinlenmesi bitmis) bir proxy
    varsa dondurur. Hicbiri uygun degilse None dondurur — caller
    `_proxy_bekleme_sn()` ile NE KADAR beklemesi gerektigini ogrenip
    bekler. Boylece karantina suresi DOLMADAN ayni IP'ler denenmez.

    `haric`: su an aktif olan proxy'yi ATLA (ayni proxy'ye geri donmeyi
    onler). Havuz bossa None."""
    if not PROXY_HAVUZU:
        return None
    # 21.09.2026 — ROTATING MODU: tek endpoint, her baglantida yeni IP.
    # Karantina mantigi BURADA UYGULANMAZ — ayni endpoint'e yeniden baglanmak
    # yeni bir IP verir, dolayisiyla "karantina" anlamsiz. Endpoint'i her
    # zaman dondururuz; sonsuz dongu korumasi cagiran tarafta
    # (_rotating_ardarda_hard) yapilir.
    if PROXY_ROTATING:
        return PROXY_HAVUZU[0]
    simdi = time.monotonic()
    uygun = [px for px in PROXY_HAVUZU
             if _proxy_durum[px]["dinlen_bitis"] <= simdi and px != haric]
    if uygun:
        return uygun[0]
    # Hicbir proxy uygun degil → None (caller beklemeli, bkz. _proxy_bekleme_sn)
    return None


def _proxy_bekleme_sn(haric=None):
    """21.09.2026 — Sonsuz failover dongusunu onlemek icin: TUM proxy'ler
    karantinadayken caller'in kac saniye beklemesi gerektigini dondurur.

    En erken bosalacak proxy'nin kalan dinlenme suresini (saniye) verir;
    hicbir proxy yoksa 30sn. Boylece bot, karantina dolmadan ayni yanmis
    IP'leri denemez — dongu kirilir.

    22.09.2026 — Karantina kaldirildigi icin (PROXY_HARD_KARANTINA_SN=30sn)
    bu fonksiyon artik pratikte 30sn'den fazla dondurmez; uzun bekleme yok."""
    if not PROXY_HAVUZU:
        return 30
    simdi = time.monotonic()
    adaylar = [px for px in PROXY_HAVUZU if px != haric] or list(PROXY_HAVUZU)
    kalan = min(max(0.0, _proxy_durum[px]["dinlen_bitis"] - simdi)
                for px in adaylar)
    # 22.09: tavan 30dk'dan 60sn'ye indirildi — uzun bekleme kaldirildi.
    return int(min(max(kalan, 30), 60))


def _proxy_hard_bildir(px):
    """21.09.2026 — ALTERNATIF COZUM 1: PX HARD BLOCK (IP karalistesi).

    22.09.2026 — KULLANICI KARARI: uzun karantina KALDIRILDI. Eskiden
    kademeli 45dk/2sa/6sa uygulaniyordu; bot saatlerce bos bekliyordu ve
    yine de PX elle gecilmek zorunda kaliniyordu. Artik her hard block'ta
    sabit 30sn — bot sirakine gecip taramaya devam eder.
    Doner: (karantina_sn, kacinci_hard).

    21.09.2026 — ROTATING MODU: tek endpoint'te karantina UYGULANMAZ.
    Cunku ayni endpoint'e yeniden baglanmak YENI bir IP verir; endpoint'i
    karantinaya almak botu tamamen durdurur. Bunun yerine ardışık hard
    block sayaci artirilir; cagiran taraf bu sayaca gore mola verir
    (bkz. _rotating_ardarda_hard). Doner: (0, n) — karantina yok."""
    if PROXY_ROTATING:
        _rotating_ardarda_hard["n"] += 1
        return (0, _rotating_ardarda_hard["n"])
    if not px or px not in _proxy_durum:
        return (PROXY_HARD_KARANTINA_SN[0], 1)
    d = _proxy_durum[px]
    d["hard_sayisi"] += 1
    idx = min(d["hard_sayisi"], len(PROXY_HARD_KARANTINA_SN)) - 1
    sure = PROXY_HARD_KARANTINA_SN[idx]
    d["dinlen_bitis"] = time.monotonic() + sure
    d["block"] = 0
    return (sure, d["hard_sayisi"])


def _proxy_block_bildir(px):
    """Bir proxy CF/block yedi — sayacini artir, esige ulastiysa dinlendir.
    True dondurur: bu proxy artik dinlenmede (caller yenisine gecmeli)."""
    if not px or px not in _proxy_durum:
        return False
    d = _proxy_durum[px]
    d["block"] += 1
    if d["block"] >= PROXY_BLOCK_ESIK:
        d["dinlen_bitis"] = time.monotonic() + PROXY_DINLENME_SN
        d["block"] = 0
        log.warning(f"[PROXY] {_proxy_kisa(px)} → {PROXY_DINLENME_SN//60}dk "
                    f"dinlendiriliyor (block esigi)")
        return True
    return False


def _proxy_basari_bildir(px):
    """Basarili sayfa — bu proxy'nin block sayacini sifirla.

    21.09.2026 — ROTATING MODU: basarili sayfa yuklemesi endpoint'in
    CALISTIGINI gosterir → ardışık hard block sayacini sifirla."""
    if PROXY_ROTATING:
        _rotating_ardarda_hard["n"] = 0
    if px and px in _proxy_durum:
        _proxy_durum[px]["block"] = 0


def _proxy_kisa(px):
    """Log icin proxy'nin sadece host:port kismini goster (kullanici:sifre gizle)."""
    if not px:
        return "proxy-yok"
    try:
        return px.split("@")[-1]
    except Exception:
        return "proxy"


def _proxy_profil_dir(px):
    """Her proxy icin AYRI Brave profili — cf_clearance cerezi karismasin.
    Proxy yoksa varsayilan profil.

    21.09.2026 — GOMULU LOGIN PROFILI: LOGIN_PROFIL_KULLAN=True ise proxy
    basina AYRI profil YERINE tek ortak login profili kullanilir. Gerekce:
    sahibinden login cerezi (PHPSESSID) HESABA baglidir, IP'ye degil —
    proxy degisse bile login oturumu gecerli kalir. Boylece kullanici bir
    kez elle login olur, tum proxyler ayni girisli profili kullanir."""
    if LOGIN_PROFIL_KULLAN:
        return LOGIN_PROFIL_DIR
    if not px:
        return ROOT / "brave_oto_profile_v2"
    # host:port'tan guvenli klasor adi turet
    ek = re.sub(r"[^0-9a-zA-Z]", "_", _proxy_kisa(px))
    return ROOT / f"brave_oto_profile_{ek}"


def _proxy_profil_temizle(px):
    """21.09.2026 — ALTERNATIF COZUM 4: PX HARD BLOCK yiyen proxy'nin
    profilini SIL. Gerekce: PX hard block sonrasi profilde `_px3`, `pxvid`,
    `_pxde` gibi PX cerezleri kalir; bunlar PX'e "bu oturum zaten supheli"
    sinyali verir. Yeni driver ayni profille acilirsa PX cerezi geri gelir
    ve hard block TEKRARLANIR. Profili silmek temiz bir oturum baslatir.

    NOT: cf_clearance cerezi de silinir — ama PX hard block zaten bu
    proxy'yi kullanilamaz hale getirdigi icin CF cerezi kaybi onemsiz
    (yeni proxy zaten yeni CF challenge'i cozer).

    Best-effort: silinemezse (dosya kilitli) sessizce devam edilir.
    Doner: True (silindi) / False (silinemedi/atlandi).

    21.09.2026 — GOMULU LOGIN PROFILI KORUMASI: LOGIN_PROFIL_KULLAN=True iken
    profil login profilidir (icinde giris cerezleri var). Bunu SILMEK
    kullaniciyi tekrar elle login olmaya zorlar — bu yuzden login profili
    ASLA silinmez. Sadece PX cerezleri (_px3/pxvid/_pxde) hedefli silinir."""
    if not px:
        return False
    # Login profili kullaniyorsak TUM profili silme — sadece PX cerezlerini
    # temizle (giris cerezleri korunsun).
    if LOGIN_PROFIL_KULLAN:
        return _login_profil_px_cerez_temizle()
    try:
        d = _proxy_profil_dir(px)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            log.warning(f"[PROXY] {_proxy_kisa(px)} profili TEMIZLENDI "
                        f"(PX cerezleri silindi — temiz oturum)")
            return True
    except Exception as e:
        log.warning(f"[PROXY] profil temizleme basarisiz ({_proxy_kisa(px)}): {e}")
    return False


def _login_profil_px_cerez_temizle():
    """21.09.2026 — GOMULU LOGIN PROFILINDE sadece PX cerezlerini sil.

    Login profilini komple silmek giris oturumunu da yok eder (kullanici
    tekrar elle login olmak zorunda kalir). Bunun yerine profildeki PX'e
    ait cerez dosyalarini (Cookies SQLite icindeki _px3/pxvid/_pxde ve
    Local Storage'daki _px*) hedefli temizleriz. Giris cerezleri
    (PHPSESSID vb.) KORUNUR.

    Best-effort: dosya kilitliyse (Brave acik) sessizce atlanir — bir
    sonraki driver kapanisinda tekrar denenir. Doner: True/False."""
    try:
        d = LOGIN_PROFIL_DIR
        if not d.exists():
            return False
        # PX cerezlerini tasiyan dosyalar: Default/Network/Cookies (SQLite),
        # Default/Local Storage/leveldb/*, Default/Session Storage/*.
        # En guvenli hedefli yaklasim: Cookies SQLite'inda PX satirlarini sil.
        cerez_db = d / "Default" / "Network" / "Cookies"
        silindi = False
        if cerez_db.exists():
            # 21.09.2026 — KRITIK DUZELTME: driver.quit() dondugunde Brave
            # sureci SQLite kilidini HEMEN birakmaz (arka planda kapaniyor).
            # Bu yuzden ilk deneme "attempt to write a readonly database"
            # hatasi veriyordu. Cozum: kisa araliklarla BIRKAC kez dene +
            # sqlite3.connect(timeout=...) ile kilit beklemesi tanimla.
            import sqlite3
            son_hata = None
            for deneme in range(5):
                try:
                    con = sqlite3.connect(str(cerez_db), timeout=5.0)
                    try:
                        cur = con.cursor()
                        # PX cerezleri: _px3, _pxvid, _pxde, pxhd, _px2, _pxff
                        cur.execute(
                            "DELETE FROM cookies WHERE host_key LIKE '%perimeterx%' "
                            "OR name LIKE '_px%' OR name LIKE 'px%'")
                        n = cur.rowcount
                        con.commit()
                    finally:
                        con.close()
                    if n and n > 0:
                        log.warning(f"[PROXY] login profilinde {n} PX cerezi "
                                    f"silindi (giris cerezleri korundu)")
                        silindi = True
                    else:
                        log.info("[PROXY] login profilinde silinecek PX cerezi yok")
                    son_hata = None
                    break   # basarili — donguden cik
                except Exception as e:
                    son_hata = e
                    # Kilitli olabilir — Brave'in kilidi birakmasi icin bekle.
                    time.sleep(1.5)
            if son_hata is not None:
                log.warning(f"[PROXY] login profil PX cerez temizligi "
                            f"basarisiz (5 deneme): {str(son_hata)[:120]}")
        return silindi
    except Exception as e:
        log.warning(f"[PROXY] login profil temizleme hatasi: {str(e)[:120]}")
        return False


# Birden fazla echo servisi — biri proxy'yi engellese/timeout olsa
# digeri devreye girsin. Sirayla denenir.
IP_ECHO_URLLER = [
    "https://api.ipify.org?format=json",
    "https://ifconfig.me/ip",
    "http://ip-api.com/json",
]


def _cikis_ip_dogrula(driver, beklenen_proxy):
    """Driver kurulduktan sonra CIKIS IP'sini kontrol et. Proxy verilmisse
    ev IP'sinden CIKMAMALI. Doner: (ip_str, proxy_calisyor_mu).
    Birden fazla echo servisi dener, hatayi ACIKCA loglar (artik sessiz yutmuyor)."""
    ip = "?"
    son_hata = None
    for url in IP_ECHO_URLLER:
        try:
            driver.set_page_load_timeout(15)
            driver.get(url)
            time.sleep(_jitter(1.5, 2.5))
            raw = driver.page_source
            m = re.search(r'(\d+\.\d+\.\d+\.\d+)', raw)
            if m:
                ip = m.group(1)
                log.info(f"[PROXY] echo servisi basarili: {url}")
                break
            else:
                log.warning(f"[PROXY] {url} yanit verdi ama IP bulunamadi "
                            f"(sayfa uzunlugu: {len(raw)} byte)")
        except Exception as e:
            son_hata = e
            log.warning(f"[PROXY] echo denemesi basarisiz ({url}): "
                        f"{type(e).__name__}: {e}")
            continue
    if ip == "?":
        log.error(f"[PROXY] TUM echo servisleri basarisiz oldu! "
                   f"Son hata: {son_hata}. Proxy baglantisinin kendisinde "
                   f"sorun olabilir (yanlis format, proxy dusuk, firewall).")
        return "?", False
    if beklenen_proxy:
        log.info(f"[PROXY] {_proxy_kisa(beklenen_proxy)} → cikis IP: {ip}")
        # 21.09.2026 — ALTERNATIF COZUM 3: exit IP'yi proxy durumuna kaydet.
        # PX karalistesi EXIT IP bazli; ayni proxy farkli zamanlarda farkli
        # exit IP verebilir (residential rotasyon). Ayni exit IP'ye tekrar
        # dusup dusmedigimizi izlemek icin kaydediyoruz.
        try:
            if beklenen_proxy in _proxy_durum:
                onceki = _proxy_durum[beklenen_proxy].get("son_exit_ip")
                _proxy_durum[beklenen_proxy]["son_exit_ip"] = ip
                if onceki and onceki == ip:
                    log.warning(f"[PROXY] {_proxy_kisa(beklenen_proxy)} AYNI "
                                f"exit IP'ye dondu ({ip}) — PX karalistesi "
                                f"riskli, dikkat")
        except Exception:
            pass
        # Ev IP'sini bilmiyoruz ama proxy verildiginde en azindan IP loglandi.
        # Caller isterse EV_IP env'i ile karsilastirir.
        ev_ip = (os.getenv("EV_IP", "") or "").strip()
        if ev_ip and ip == ev_ip:
            log.error(f"[PROXY] ⚠️ CIKIS IP EV IP'SI ({ip}) — PROXY CALISMIYOR!")
            return ip, False
        return ip, True
    log.info(f"[PROXY] (proxysiz) cikis IP: {ip}")
    return ip, True


_BRAVE_PROFIL = ROOT / "brave_oto_profile_v2"

# ══════════════════════════════════════════════════════════════════════
# 21.09.2026 — GOMULU SAHIBINDEN LOGIN PROFILI (kullanici stratejisi).
# ══════════════════════════════════════════════════════════════════════
# Kullanici karari: "gomulu bir sahibinden profili cakip dedigim gibi yine
# bot patlarsa uzaktan baglanti ile isi cevirmeye calisacagiz."
#
# MANTIK: sahibinden login duvari (Cloudflare Turnstile + reCAPTCHA + SMS/2FA)
# bot tarafindan COZULEMEZ. Ama bir kez ELLE login olunursa oturum cerezleri
# (PHPSESSID vb.) profilde kalir; sonraki acilislar login duvarini GORMEZ.
#
# Bu yuzden AYRI bir "login profili" tutuyoruz:
#   - Kullanici bir kez bu profille acip ELLE login olur (bot bunu bekler).
#   - Cerezler profilde kalir → sonraki acilislar girisli baslar.
#   - Login duvari YINE cikarsa: pencere one getirilir, uzun manuel pencere
#     acilir (kullanici uzaktan baglanip cozer).
#
# LOGIN_PROFIL_KULLAN: True ise bot bu profili kullanir (proxy basina DEGIL,
# tek ortak login profili — cunku login cerezi IP'ye bagli degil, hesaba
# bagli). False ise eski davranis (proxy basina ayri profil).
LOGIN_PROFIL_KULLAN = (os.getenv("LOGIN_PROFIL_KULLAN", "1") or "1").strip().lower() \
    in ("1", "true", "evet", "yes")
LOGIN_PROFIL_DIR = ROOT / "brave_oto_profile_login"
# Login duvari cikinca kullaniciya verilecek manuel cozme penceresi (sn).
# Uzaktan baglanti icin makul bir sure (kullanici "uzaktan baglanti ile isi
# cevirmeye calisacagiz" dedi — bu yuzden genis tutuldu).
LOGIN_MANUEL_PENCERE_SN = int(os.getenv("LOGIN_MANUEL_PENCERE_SN", "300") or "300")


# ══════════════════════════════════════════════════════════════════════
# 21.09.2026 — PROXY KIMLIK DOGRULAMA OTOMASYONU (auth popup'ini kaldirir)
# ══════════════════════════════════════════════════════════════════════
# SORUN: --proxy-server=http://kullanici:sifre@host:port verildiginde
# Chromium URL'deki kimlik bilgilerini YOK SAYAR — sadece host:port alir ve
# ekrana "Proxy kimlik dogrulamasi" POPUP'i cikarir. Bu popup elle
# onaylanmadan hicbir sayfa acilmaz; bot tamamen takilir.
#
# COZUM: Chromium'un proxy auth'unu otomatik besleyen bir UZANTI (extension)
# yuklemek. Manifest V2 + chrome.webRequest.onAuthRequired ile kimlik
# bilgileri SESSIZCE verilir — popup hic cikmaz. Bu, Selenium/uc ile proxy
# auth'un standart cozumudur (CDP'de dogrudan proxy auth API'si yok).
#
# NOT: Manifest V2 kullaniyoruz cunku onAuthRequired blocking webRequest
# gerektirir; MV3'te bu declarativeNetRequest ile proxy auth saglamaz.
# Brave/Chromium MV2'yi hala destekler (2026 itibariyla).


def _proxy_parcala(proxy):
    """'http://kullanici:sifre@host:port' → (host, port, kullanici, sifre).
    Kimlik bilgisi yoksa kullanici/sifre None doner."""
    if not proxy:
        return None, None, None, None
    p = proxy.strip()
    # semayi at
    if "://" in p:
        p = p.split("://", 1)[1]
    kullanici = sifre = None
    if "@" in p:
        kimlik, p = p.rsplit("@", 1)
        if ":" in kimlik:
            kullanici, sifre = kimlik.split(":", 1)
        else:
            kullanici = kimlik
    if ":" in p:
        host, port = p.rsplit(":", 1)
    else:
        host, port = p, "80"
    return host, port, kullanici, sifre


def _proxy_auth_uzanti_olustur(proxy):
    """Proxy kimlik bilgilerini otomatik besleyen MV2 uzantisi uret.
    Doner: uzanti klasoru Path | None (kimlik yoksa None)."""
    host, port, kullanici, sifre = _proxy_parcala(proxy)
    if not (kullanici and sifre):
        return None   # kimlik yok — uzantiya gerek yok
    uzanti_dir = ROOT / "px_proxy_auth_ext"
    uzanti_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0",
    }
    background = """
var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: { scheme: "http", host: "%s", port: parseInt("%s") },
        bypassList: ["localhost"]
    }
};
chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
function callbackFn(details) {
    return { authCredentials: { username: "%s", password: "%s" } };
}
chrome.webRequest.onAuthRequired.addListener(
    callbackFn,
    { urls: ["<all_urls>"] },
    ['blocking']
);
""" % (host, port, kullanici, sifre)
    try:
        with open(uzanti_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        with open(uzanti_dir / "background.js", "w", encoding="utf-8") as f:
            f.write(background)
        log.info(f"[PROXY] auth uzantisi uretildi ({_proxy_kisa(proxy)})")
        return uzanti_dir
    except Exception as e:
        log.warning(f"[PROXY] auth uzantisi uretilemedi: {e}")
        return None


def _driver_olustur(proxy=None):
    """21.09.2026 — FINGERPRINT SERTLESTIRILDI (PerimeterX savunmasi).

    Onceki hali sadece AutomationControlled kapatip sabit 1366x800 pencere
    aciyordu. PX icin bu YETERSIZ: cikis IP'si TR iken tarayicinin
    timezone/locale'i varsayilan (en-US/UTC) kalirsa PX bunu aninda
    'bot' isaretler. Burada:
      - timezone = Europe/Istanbul (proxy cikis IP'siyle TUTARLI)
      - locale/lang = tr-TR (Accept-Language + navigator.language)
      - pencere boyutu her oturumda hafif farkli (sabit boyut = parmak izi)
      - CDP sizintilari (webdriver, chrome.runtime, permissions, WebGL)
        hem uc'nin kendi yamalariyla hem ek PX_STEALTH_JS ile kapatilir.
    """
    opts = uc.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-dev-shm-usage")
    # Pencere boyutu — sabit degil, yaygin cozunurluklerden rastgele secim
    w, h = random.choice(PX_PENCERE_SECENEKLERI)
    opts.add_argument(f"--window-size={w},{h}")
    # Dil / locale — TR cikis IP'siyle tutarli
    opts.add_argument(f"--lang={PX_LOCALE}")
    opts.add_argument(f"--accept-lang={PX_LANG}")
    # Timezone — Chromium'a dogrudan verilemez, CDP ile set edilir (asagida).
    # Ama TZ env'i de set edelim (bazi Chromium surumleri okur).
    opts.add_argument("--disable-features=IsolateOrigins,site-per-process")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    # ── PROXY: kimlik dogrulama popup'ini OTOMATIK besle ───────────────
    # 21.09.2026 — --proxy-server URL'indeki kullanici:sifre'yi YOK SAYAR
    # ve popup cikarir. Bu yuzden kimlik varsa proxy'yi UZANTI uzerinden
    # veriyoruz (hem proxy hem auth'u uzanti ayarlar, popup cikmaz).
    # Kimlik yoksa eski yontem (--proxy-server) yeterli.
    _auth_ext = _proxy_auth_uzanti_olustur(proxy) if proxy else None
    if _auth_ext is not None:
        # Chromium 137+ --load-extension'i guvenlik icin ENGELLER. Bu
        # feature flag'i kapatinca tekrar izin verilir (Brave 153 icin sart).
        opts.add_argument(
            "--disable-features=DisableLoadExtensionCommandLineSwitch")
        opts.add_argument(f"--load-extension={_auth_ext}")
        opts.add_argument(f"--disable-extensions-except={_auth_ext}")
        log.info("[PROXY] proxy auth uzanti ile veriliyor (popup cikmayacak)")
    elif proxy:
        # Kimlik yok — duz proxy-server yeterli
        opts.add_argument(f"--proxy-server={proxy}")
    if ANONIM_MOD:
        # Kalici profil YOK — damga birikmesin (bkz. ANONIM_MOD tanimi).
        log.info("[PROFIL] ANONIM mod — kalici profil kullanilmiyor, "
                 "her acilista temiz gecici profil")
    else:
        # KALICI profil — cf_clearance/PX cerezi restart'lar arasinda yasasin.
        # Her proxy AYRI profil kullanir (cerezler karismasin).
        profil_dir = _proxy_profil_dir(proxy)
        profil_dir.mkdir(parents=True, exist_ok=True)
        opts.add_argument(f"--user-data-dir={profil_dir}")
    kwargs = dict(
        options=opts,
        browser_executable_path=BRAVE_PATH,
        version_main=VERSION_MAIN,
        no_sandbox=False,   # Brave 150+ icin crash korumasi (uc'nin kendi param'i)
    )
    if _CHROMEDRIVER_LOCAL.exists():
        # Path acikca verildi → patcher.auto() network'e gitmeden lokal patch yapar
        kwargs["driver_executable_path"] = str(_CHROMEDRIVER_LOCAL)
    driver = uc.Chrome(**kwargs)
    # 22.09.2026 — CHROMEDRIVER CLIENT TIMEOUT'U KISALTILDI (120sn → 35sn).
    # Saha gozlemi: residential proxy oturum ORTASINDA TCP baglantisini yarim
    # birakiyor; o andaki tarayici komutu (page_source, ActionChains, CDP)
    # asili kaliyor. Navigasyon icin watchdog var (NAVIGATE_WATCHDOG_SN) ama
    # yuklemeden SONRAKI cagrilar korumasiz — orada selenium'un varsayilan
    # 120sn'lik HTTP read timeout'u devreye giriyordu. 35sn'ye cekilince
    # takilma 3.5 kat daha erken fark ediliyor; ana dongu driver'i yenileyip
    # devam ediyor (bkz. "Chromedriver cevap vermiyor" dali).
    # NOT: normal komutlar milisaniyeler surer; 35sn zaten cok genis bir pay.
    try:
        driver.command_executor.client_config.timeout = DRIVER_CLIENT_TIMEOUT_SN
    except Exception as e:
        log.warning(f"[DRIVER] client timeout ayarlanamadi: {e}")
    # 23.09.2026 — SAYFA YUKLEME SINIRI (kritik bosluk kapatildi).
    # Bulgu: set_page_load_timeout() kodda SADECE proxy cikis dogrulamasinin
    # icinde cagriliyordu. Proxy varken tesadufen ayarlanmis oluyordu;
    # PROXYSIZ calisirken o fonksiyon hic calismiyor → ana driver'a hicbir
    # yukleme siniri konmuyor → navigasyon SONSUZA KADAR bekleyebiliyor.
    # Olculen: proxysiz donemde asilma orani 4 KAT yuksekti. Artik her
    # driver kurulumunda acikca ayarlaniyor.
    try:
        driver.set_page_load_timeout(SAYFA_YUKLEME_TAVANI_SN)
    except Exception as e:
        log.warning(f"[DRIVER] page load timeout ayarlanamadi: {e}")
    # ── CDP tabanli fingerprint zorlamasi ──────────────────────────────
    # Chromium timezone/locale'i komut satirindan almaz; CDP ile set edilir.
    # Bu, PX'in en cok baktigi sinyal (IP ↔ timezone tutarliligi).
    try:
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride",
                               {"timezoneId": PX_TIMEZONE})
    except Exception as e:
        log.warning(f"[PX] timezone override basarisiz: {e}")
    try:
        driver.execute_cdp_cmd("Emulation.setLocaleOverride",
                               {"locale": PX_LOCALE})
    except Exception as e:
        log.warning(f"[PX] locale override basarisiz: {e}")
    # ── Stealth JS — her yeni dokumanda calissin ───────────────────────
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                               {"source": PX_STEALTH_JS})
    except Exception as e:
        log.warning(f"[PX] stealth JS enjekte edilemedi: {e}")
    log.info(f"[PX] fingerprint hazir — tz={PX_TIMEZONE} locale={PX_LOCALE} "
             f"pencere={w}x{h} proxy={_proxy_kisa(proxy) if proxy else 'yok'}")
    return driver


def _driver_ayakta_mi(driver):
    if driver is None:
        return False
    try:
        _ = driver.window_handles
        return True
    except Exception:
        return False


def _cf_gec(driver, max_bekleme=120):
    """Ilk acilis CF/PX karsilama — SABIRLI. Yeni istek atmaz, sadece mevcut
    sayfanin title'ini 10sn araliklarla izler. Kullanici manuel 'basili tut'
    yapana kadar bekler, panik yapip tekrar denemez.

    21.09.2026 — PX FARKINDALIGI: eskiden SADECE CF title'i kontrol ediliyordu.
    PX challenge'i title'i degistirmeden DOM'a enjekte ederse bu fonksiyon
    'gecildi' sanip devam ediyordu. Simdi PX de kontrol ediliyor ve ilk
    turda OTOMATIK cozum deneniyor (_px_coz).

    21.09.2026 — PX HARD BLOCK FARKINDALIGI (KRITIK DUZELTME): saha logu
    gosterdi ki acilista PX HARD BLOCK (title='Access to this page has been
    denied', iframe display:none) yendiginde bu fonksiyon onu 'captcha'
    sanip _px_coz() ile 3 kez bos yere deniyor, sonra 'kullaniciya birak'
    diyip DONUYORDU. Boylece proxy failover HIC devreye girmiyor ve bot
    yanmis IP'de takili kaliyordu. Artik: hard block ise _px_coz() HIC
    denenmez, fonksiyon hemen False doner → ana dongu proxy'yi karantinaya
    alip degistirir. Doner: True (sayfa hazir) / False (hard block veya
    cozulemedi — caller proxy degistirmeli).

    21.09.2026 — LOGIN DUVARI FARKINDALIGI (KRITIK DUZELTME): saha logu
    (dump 20260921_164324_giris) gosterdi ki sahibinden bazi IP'lerde
    dogrudan LOGIN SAYFASINA yonlendiriyor (title='sahibinden.com Giriş',
    Cloudflare Turnstile + reCAPTCHA korumali, returnUrl=hedef URL).

    21.09.2026 — KULLANICI TALEBI: login duvari gorulunce bot DURMALI,
    surekli istek atmamali. Bu yuzden login duvari icin ozel "giris"
    sinyali doneriz (False DEGIL) → ana dongu proxy DEGISTIRMEZ, sadece
    pencereyi one getirip kullanici ELLE giris yapana kadar bekler.

    Doner: True (sayfa hazir) / False (hard block veya cozulemedi — caller
    proxy degistirmeli) / "giris" (login duvari — caller DURMALI, istek YOK)."""
    bekledi = 0
    px_denendi = False
    while bekledi < max_bekleme:
        try:
            title = (driver.title or "").lower()
            html = driver.page_source
        except Exception:
            title, html = "", ""
        cf_isareti = _cf_title_mi(title)
        # 21.09.2026 — ONCE hard block kontrolu (cozulemez, proxy degistir).
        if _px_hard_mi(html, title):
            log.error("  🚫 PX HARD BLOCK (acilis) — IP karalistesi, "
                      "cozulemez; proxy degistirilecek")
            return False
        # 21.09.2026 — LOGIN DUVARI (acilis): sahibinden bu IP'de dogrudan
        # giris sayfasina yonlendirdi. Cozulemez (SMS/2FA gerekir).
        #
        # 21.09.2026 — KULLANICI TALEBI (KRITIK): "login ekrani gelince DUR,
        # surekli istek atma." Eskiden burada False donup ana dongu proxy
        # degistirip TEKRAR driver.get() atiyordu → login duvari yine →
        # KISIR DONGU (her istek durumu kotulestiriyor). Artik ozel "giris"
        # sinyali doneriz; ana dongu proxy DEGISTIRMEZ, sadece pencereyi one
        # getirip kullanici ELLE giris yapana kadar bekler (istek YOK).
        if ("sahibinden.com giriş" in title or "sahibinden.com giris" in title
                or "2 aşamalı doğrulama" in title
                or "2 asamali dogrulama" in title):
            log.error("  🔐 LOGIN DUVARI (acilis) — sahibinden giris istiyor. "
                      "BOT DURDURULDU: istek ATILMAYACAK, ELLE giris bekleniyor.")
            return "giris"
        px_isareti = _px_mi(html, title)
        if title and not cf_isareti and not px_isareti:
            return True
        if px_isareti and not px_denendi:
            # 22.09.2026 — OTOMATIK COZUM DEVRE DISI (olculdu, hic ise
            # yaramadi). Tum log gecmisi tarandi: 33 deneme, 0 basari,
            # 12 kez "iframe hic acilmadi". Her PX olayinda 3 deneme x ~10sn
            # bosa gidiyor, sonra zaten kullaniciya birakiliyordu. Artik
            # dogrudan kullaniciya birakiliyor — 30sn daha erken.
            px_denendi = True
            log.warning("  🖐️  PerimeterX challenge (acilis) — PENCEREDE "
                        "'Basılı Tut' dugmesine bas (otomatik cozum kapali)")
        elif bekledi == 0 and cf_isareti:
            log.warning("⚠️ Cloudflare challenge — pencerede 'I am human' tikla")
        time.sleep(10)
        bekledi += 10
    return False


def _sayfa_durumu(html, title):
    """Bir sayfa cekiminden sonra durumu sinifla: 'ok' | 'cf' | 'block' | 'bos'.
    Ana dongu ve detay worker bu fonksiyona gore geri cekilme kararini verir.

    NOT: body-content string aramasi ("turnstile" vb.) BILEREK kullanilmiyor.
    Test sirasinda gercek bir liste sayfasinda id="turnStileArea" (gizli,
    display:none login popup widget'i) bulundu — "turnstile" alt-string'i
    yanlis pozitif uretti (CLAUDE.md'deki cloudflareinsights.com dersiyle
    ayni tuzak). Title kontrolu tek basina kanitlanmis guvenilir sinyal."""
    t = (title or "").lower()
    h = html or ""
    # CF challenge — SADECE title (body-content arama yanlis pozitif riski tasir)
    if _cf_title_mi(t):
        return "cf"
    # 19.09.2026 — LOGIN DUVARI / 2FA. Endpoint testinde 12 kez goruldu ve
    # eskiden 'bos' saniliyordu (boyut ~100KB, yani block esiginin ustunde,
    # ama searchResultsItem yok). Bunlar bottan cozulebilecek seyler DEGIL —
    # kullanicinin pencerede giris yapmasi/dogrulamasi gerekiyor. Bu sirada
    # istek atmak durumu kotulestirir, o yuzden ayri durum olarak isaretleniyor.
    # Gercek liste sayfasinin title'i "...sahibinden.com'da" seklinde, bu
    # stringleri ICERMEZ — yanlis pozitif riski yok.
    if "2 aşamalı doğrulama" in t or "2 asamali dogrulama" in t:
        return "2fa"
    if "sahibinden.com giriş" in t or "sahibinden.com giris" in t:
        return "giris"
    # 20.09.2026 — PERIMETERX/HUMAN "Press & Hold" captcha. Bu CLOUDFLARE
    # DEGIL — sahibinden'in kullandigi ayri, kurumsal bir bot-tespit urunu
    # (px-cloud.net, px-captcha). Duvar dump ile dogrulandi (bkz. CLAUDE.md
    # 20.09 notu). Eskiden bu "block" saniliyordu (HTML kucuk oldugu icin
    # esik altinda kalıyordu) ve "cozulemez sert red" muamelesi goruyordu —
    # oysa icinde TIKLANIP BASILI TUTULABILEN gercek bir dugme var.
    #
    # 21.09.2026 — ARTIK SADECE TITLE DEGIL, DOM DA KONTROL EDILIYOR.
    # Duvar dump analizi (20260921_*_captcha.txt) gosterdi ki PX bazi
    # challenge'larda title'i DEGISTIRMEZ, sadece DOM'a enjekte eder
    # (meta content="px-captcha", div#px-captcha-wrapper, js.px-cloud.net
    # iframe). _px_mi() hem title hem DOM imzasini kontrol eder. DOM
    # imzalari PX'in KENDI artefaktlari — gercek liste sayfasinda bulunmaz
    # (dump ile dogrulandi), yanlis pozitif riski yok.
    # 21.09.2026 — PX HARD BLOCK (IP karalistesi) — 'captcha'dan ONCE kontrol
    # edilmeli. "Access to this page has been denied" title'i hem PX_TITLE_
    # ISARETLERI'nde (captcha) hem PX_HARD_TITLE_ISARETLERI'nde (px_hard)
    # gecer; px_hard DAHA SPESIFIK oldugu icin once o secilir. Boylece
    # cozulemez hard block, proxy failover tetikleyen ayri bir duruma gider.
    if _px_hard_mi(h, t):
        return "px_hard"
    if _px_mi(h, t):
        return "captcha"
    # soft-block / bos icerik
    if len(h) < BLOCK_HTML_ESIK:
        return "block"
    if "searchResultsItem" not in h:
        return "bos"
    return "ok"


def _detay_sayfa_durumu(html, title):
    """Detay sayfasi icin durum tespiti — 'ok' | 'cf' | 'captcha' | 'block'.
    Liste sayfasindaki searchResultsItem kontrolu burada YOK (detay
    sayfasinda zaten bulunmaz, 'bos' kavrami gecerli degil).

    21.09.2026 — PX eklendi. Detay sayfalari da PX challenge'i yiyebilir;
    eskiden bu 'block' sanilip proxy failover tetikliyordu (yanlis — PX
    IP'ye ozgu sert red degil, oturuma baglı interaktif dogrulama)."""
    t = (title or "").lower()
    h = html or ""
    if _cf_title_mi(t):
        return "cf"
    # 21.09.2026 — PX hard block (IP karalistesi) 'captcha'dan ONCE.
    if _px_hard_mi(h, t):
        return "px_hard"
    if _px_mi(h, t):
        return "captcha"
    if len(h) < BLOCK_HTML_ESIK:
        return "block"
    return "ok"


def _duyarli_bekle(saniye, stop_event=None):
    """time.sleep yerine — stop_event set edilirse hemen doner (Ctrl+C/kapanis)."""
    end = time.monotonic() + saniye
    while time.monotonic() < end:
        if stop_event is not None and stop_event.is_set():
            return
        time.sleep(min(5, max(0, end - time.monotonic())))


_DUVAR_DUMP_KLASOR = ROOT / "duvar_dump"
_duvar_dump_sayac = 0


def _duvar_dump_yaz(durum, title, html, tavan=40):
    """20.09.2026 — TANI amacli. durum!='ok' oldugunda title + html'in
    basini/turnstile/challenge gecen kisimlarini diske yazar. Amac: sahada
    gorulen 'basili tut' ekraninin GERCEK title'ini ve DOM yapisini
    yakalamak — bugune kadar sadece byte boyutuna ve tahmine dayaniyorduk.
    En fazla `tavan` dosya tutulur (disk sismesini onlemek icin), sonra
    en eskisi sessizce atlanir (silinmez, elle temizlenir)."""
    global _duvar_dump_sayac
    if _duvar_dump_sayac >= tavan:
        return
    try:
        _DUVAR_DUMP_KLASOR.mkdir(parents=True, exist_ok=True)
        zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
        dosya = _DUVAR_DUMP_KLASOR / f"{zaman}_{durum}.txt"
        h = html or ""
        # turnstile/challenge/captcha gecen satirlari da ayrica cikar —
        # HTML devasa olabilir, ilgili kisimlari bulmak kolaylassin
        ilgili = [ln for ln in h.splitlines()
                  if any(k in ln.lower() for k in
                         ("turnstile", "challenge", "captcha", "cf-", "cdn-cgi"))]
        with open(dosya, "w", encoding="utf-8") as f:
            f.write(f"durum : {durum}\n")
            f.write(f"title : {title!r}\n")
            f.write(f"html_boyut: {len(h)} byte\n")
            f.write(f"zaman : {datetime.now().isoformat()}\n")
            f.write("\n--- HTML ILK 3000 KARAKTER ---\n")
            f.write(h[:3000])
            f.write("\n\n--- turnstile/challenge/captcha/cf- GECEN SATIRLAR ---\n")
            f.write("\n".join(ilgili[:50]))
        _duvar_dump_sayac += 1
        log.info(f"  🔎 [TANI] {durum} durumu diske yazildi: {dosya.name}")
    except Exception as e:
        log.warning(f"  [TANI] duvar dump yazilamadi: {e}")


def _sayfa_duzeldi_mi(driver, max_bekleme, takili=("block",), stop_event=None,
                      aralik=15, beklenen=None):
    """19.09.2026 — engel ekranindayken YENI ISTEK ATMADAN acik sayfayi izler.

    Gerekce: engellerin bir kismi kullanicinin ekranda cozdugu seyler
    (basili tut challenge, login duvari, 2FA). Kullanici cozunce sayfa KENDI
    KENDINE degisir. Eskiden kor uyuyorduk; bunu kacirip sure dolunca yeni
    istek atiyorduk, bu da durumu tazeliyordu. Burada HIC navigasyon yok,
    sadece acik sayfanin title/source'u okunuyor.

    takili: hangi durumlar "hala engelli" sayilacak. Durum bu kumeden
    cikarsa True doner (block icin 'ok' beklenir; login icin kullanici giris
    yapinca sayfa ana sayfaya gidebilir, o da 'bos' olur ama 'giris' degildir
    — yani duvar asilmistir).

    beklenen: (21.09.2026 — KRITIK DUZELTME) Sadece "takili degil" yetmez.
    LOGIN DUVARI sayfasi (sahibinden.com/giris) 'searchResultsItem'
    ICERMEDIGI icin _sayfa_durumu ona 'bos' der — 'bos' da takili kumesinde
    olmadigindan eski kod ANINDA True donuyordu ve bot login duvarini
    "asildi" sanip Tur #1'e geciyordu (log kaniti: 01:16:53 duvar → 01:17:38
    'Giris yapildi' → Tur #1, sadece 45sn sonra). Artik beklenen verilirse
    SADECE o durum(lar) gorulunce True doner; yani login icin 'ok' (gercek
    liste sayfasi) beklenir. beklenen=None ise eski davranis korunur.

    Doner: True (duvar asildi) / False (sure doldu)."""
    gecen = 0
    while gecen < max_bekleme:
        adim = min(aralik, max_bekleme - gecen)
        _duyarli_bekle(adim, stop_event)
        gecen += adim
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            html = driver.page_source
            title = driver.title or ""
        except Exception:
            # 23.09.2026 — tarayici olduyse bosuna bekleme (bkz.
            # _login_duvari_asildi_mi'deki ayni duzeltme). Ayaktaysa gecici
            # bir cevapsizliktir, beklemeye devam.
            if not _driver_ayakta_mi(driver):
                log.warning("  [BEKLEME] tarayici kapanmis — bekleme "
                            "sonlandirildi, driver yeniden kurulacak")
                return False
            continue
        durum = _sayfa_durumu(html, title)
        if beklenen is not None:
            # Sadece GERCEKTEN istenen durum gorulunce basarili say.
            if durum in beklenen:
                return True
        else:
            if durum not in takili:
                return True
    return False


def _login_duvari_asildi_mi(driver, max_bekleme, stop_event=None, aralik=15):
    """21.09.2026 — LOGIN/2FA duvari icin OZEL bekleme (istek YOK).

    Neden ayri fonksiyon: _sayfa_duzeldi_mi'nin 'beklenen=("ok",)' mantigi
    login sonrasi YANLIS olurdu — kullanici giris yapinca sayfa genelde ANA
    SAYFAYA (sahibinden.com/) duser, ana sayfada 'searchResultsItem' YOKTUR,
    yani _sayfa_durumu ona 'bos' der ve bot SONSUZA KADAR beklerdi.

    Dogru kriter: sayfa artik BILINEN-KOTU durumlardan HICBIRI degilse duvar
    asilmistir. Kotu durumlar: giris, 2fa, block, bos, cf, captcha, px_hard.
    Yani sayfa gercek bir sahibinden sayfasi (ana sayfa VEYA liste) olmali.
    'bos' de kotu sayilir cunku login sayfasi da 'bos' doner (bkz. asil bug).

    Doner: True (duvar asildi) / False (sure doldu / kapanis)."""
    kotu = ("giris", "2fa", "block", "bos", "cf", "captcha", "px_hard")
    gecen = 0
    while gecen < max_bekleme:
        adim = min(aralik, max_bekleme - gecen)
        _duyarli_bekle(adim, stop_event)
        gecen += adim
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            html = driver.page_source
            title = driver.title or ""
        except Exception:
            # 23.09.2026 — TARAYICI OLDU MU? (saha bulgusu)
            # Eskiden burada kosulsuz `continue` vardi: kullanici pencereyi
            # kapatirsa (veya Brave cokerse) bot OLU bir driver'i 24 SAAT
            # boyunca izlemeye devam ediyordu — log sessiz, veri sifir,
            # kendiliginden kurtulmuyordu. Sahada aynen gozlendi: kullanici
            # bir popup'i iptal etti, Brave kapandi, bot 10 dk boyunca bos
            # bekledi. Artik driver olduyse hemen cikiyoruz; ana dongu
            # yeni driver kurup devam ediyor.
            if not _driver_ayakta_mi(driver):
                log.warning("  [BEKLEME] tarayici kapanmis — bekleme "
                            "sonlandirildi, driver yeniden kurulacak")
                return False
            continue
        durum = _sayfa_durumu(html, title)
        if durum not in kotu:
            # 'ok' (liste) veya baska gecerli bir sahibinden sayfasi.
            return True
    return False


def _cf_bekle(driver, stop_event=None, ana_sekme_ref=None):
    """CF challenge tespit edildi — YENI ISTEK ATMADAN kullanicinin manuel
    gecmesini bekle. Pencereyi restore eder (minimize'dan cikar).
    Donus: True (gecti) / False (CF_MAX_DENEME basarisiz).

    ana_sekme_ref: worker ayri sekmede calisiyor olabilir — title okumadan
    once ana sekmeye EXPLICIT donuyoruz, yoksa worker'in sekmesini okuyabiliriz."""
    log.warning("[CF] challenge tespit edildi — pencere acik, "
                "manuel 'basılı tut' / 'I am human' bekleniyor. "
                "Baska istek ATILMIYOR.")
    try:
        driver.restore_window()
    except Exception:
        try: driver.maximize_window()
        except Exception: pass

    beklemeler = [CF_ILK_BEKLEME_SN] + [CF_TEKRAR_BEKLEME_SN] * (CF_MAX_DENEME - 1)
    for i, bekleme in enumerate(beklemeler, 1):
        log.info(f"[CF] {bekleme}sn bekleniyor (kontrol {i}/{CF_MAX_DENEME})...")
        _duyarli_bekle(bekleme, stop_event)
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            if ana_sekme_ref and ana_sekme_ref.get("handle"):
                driver.switch_to.window(ana_sekme_ref["handle"])
            title = (driver.title or "").lower()
        except Exception:
            title = ""
        cf_hala = _cf_title_mi(title)
        if title and not cf_hala:
            log.info(f"[CF] ✓ Gecildi (kontrol {i}/{CF_MAX_DENEME})")
            try: driver.minimize_window()
            except Exception: pass
            return True
        log.warning(f"[CF] hala challenge ekraninda (kontrol {i}/{CF_MAX_DENEME})")

    log.error(f"[CF] {CF_MAX_DENEME} kontrol basarisiz → 30 dk mola")
    return False


# 18.09.2026 — INSAN SIMULASYONU. Sayfa DOM'a dusunce eskiden sadece
# pasif sleep vardi, hic etkilesim yoktu — bu, CF'nin JS-tabanli bot
# skorlamasinin (mouse/scroll/sure event'leri) kolayca ayirt edebilecegi
# bir imzaydi. Simdi: okuma beklemesi + rastgele scroll + opsiyonel mouse
# hareketi. Hatalar (elementler DOM'da yoksa vb.) sessizce yutulur —
# simulasyon basarisiz olsa bile tarama akisi bozulmamali.
def _insan_gibi_davran(driver):
    """18.09.2026 — scroll'u execute_script(window.scrollBy) YERINE gercek
    klavye girdisi (PAGE_DOWN/DOWN) ile yapiyoruz. Gerekce: execute_script
    ile tetiklenen scroll event'i tarayicida isTrusted=false olarak
    isaretlenir (JS'ten enjekte edildigi icin) — elle scroll ise
    isTrusted=true. Gelismis bot tespiti bu farki dogrudan olcebilir; JS
    enjeksiyonuyla yapilan "insan simulasyonu" aslinda kendi ihanet
    imzasini uretiyor olabilir. ActionChains.send_keys ise W3C Actions
    API/CDP Input domain'i uzerinden dispatch edilir — tarayici tarafinda
    gercek girdiye çok daha yakin degerlendirilir."""
    time.sleep(_jitter(OKUMA_BEKLEME_MIN, OKUMA_BEKLEME_MAX))

    try:
        ilanlar_el = driver.find_elements(By.CSS_SELECTOR, "tr.searchResultsItem")
    except Exception:
        ilanlar_el = []

    n_scroll = random.randint(SCROLL_SAYISI_MIN, SCROLL_SAYISI_MAX)
    for _ in range(n_scroll):
        # 20.09.2026 — CF teshisi PerimeterX/HUMAN oldugunu ortaya cikardi
        # (bkz. CLAUDE.md). Bu urunler surekli mouse hareketi telemetrisine
        # agirlik verir; eskiden %40 ihtimalle TEK bir "isinlanma" hareketi
        # vardi (bir noktadan dogrudan hedefe atlama) — gercek kullanicidan
        # cok farkli bir imza. Her scroll'dan once artik ARA NOKTALI, kucuk
        # gecikmeli birkac hareket var.
        _mikro_mouse_hareketleri(driver, ilanlar_el)
        try:
            tus = Keys.PAGE_DOWN if random.random() < 0.7 else Keys.DOWN
            ActionChains(driver).send_keys(tus).perform()
        except Exception:
            pass
        time.sleep(_jitter(SCROLL_BEKLEME_MIN, SCROLL_BEKLEME_MAX))

    _mikro_mouse_hareketleri(driver, ilanlar_el)


def _isinma_turu(driver, stop_event=None):
    """21.09.2026 — KARANTINA SONRASI 'ISINMA' TURU (PX davranissal savunma).

    Saha gozlemi: proxy karantinadan cikip dogrudan listeye dalinca PX
    ~2 tur icinde HARD BLOCK veriyordu. Gercek kullanici bir siteye
    "soguk" girmez — once ana sayfaya bakar, gezinir, sonra arama yapar.
    Bu fonksiyon, yeni bir proxy/driver kuruldugunda (veya karantina
    sonrasi ilk turda) listeye dalmadan ONCE ana sayfada birkac saniye
    insan gibi davranir; PX'in "yeni oturum" puanini yukseltir.

    Yapilanlar (hepsi gercek girdi — isTrusted=true):
      1. Ana sayfaya git, birkac saniye bekle.
      2. Biraz asagi/yukari scroll (PAGE_DOWN/PAGE_UP).
      3. Fareyi sayfa uzerinde ara noktali hareketlerle dolastir.
      4. Kucuk bir okuma molasi.

    Hata durumunda SESSIZCE doner — isinma turu basarisiz olsa bile
    tarama akisini ENGELLEMEZ (best-effort)."""
    try:
        log.info("  [ISINMA] Karantina sonrasi isinma turu — ana sayfada "
                 "insan gibi davraniliyor")
        driver.get("https://www.sahibinden.com/")
        time.sleep(_jitter(3.0, 6.0))
        # Birkac scroll (yukari/asagi karisik — tek yonlu desen bot imzasi)
        for _ in range(random.randint(2, 4)):
            try:
                tus = Keys.PAGE_DOWN if random.random() < 0.7 else Keys.PAGE_UP
                ActionChains(driver).send_keys(tus).perform()
            except Exception:
                pass
            time.sleep(_jitter(1.0, 2.5))
        # Fareyi sayfa uzerinde dolastir (ara noktali)
        try:
            govde = driver.find_elements(By.CSS_SELECTOR, "body")
            _mikro_mouse_hareketleri(driver, govde)
        except Exception:
            pass
        time.sleep(_jitter(2.0, 4.0))
        log.info("  [ISINMA] Tamamlandi — taramaya geciliyor")
    except Exception as e:
        log.warning(f"  [ISINMA] Atlandi (hata): {str(e)[:120]}")


def _mikro_mouse_hareketleri(driver, elementler):
    """20.09.2026 — birden fazla ilan uzerinden ARA ADIMLARLA gecen fare
    hareketi. Tek 'move_to_element' cagrisi bir noktadan digerine aninda
    isinlanir (tarayici JS'i icin tek bir mousemove olayi) — gercek insan
    faresi onlarca ara mousemove event'i uretir. Burada birden fazla hedefe
    ufak ofsetlerle ugrayip aralarda kisa duraklamalar ekleniyor; hala tek
    bir ActionChains.perform() cagrisi ile W3C Actions/CDP Input uzerinden
    dispatch ediliyor (trusted input, bkz. scroll'daki ayni gerekce)."""
    if not elementler:
        return
    try:
        n = random.randint(MIKRO_MOUSE_MIN, MIKRO_MOUSE_MAX)
        secilenler = random.sample(elementler, min(n, len(elementler)))
        actions = ActionChains(driver)
        for el in secilenler:
            ofset_x = random.randint(-15, 15)
            ofset_y = random.randint(-5, 5)
            actions.move_to_element(el)
            actions.move_by_offset(ofset_x, ofset_y)
            actions.pause(random.uniform(0.05, 0.25))
        actions.perform()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 21.09.2026 — PERIMETERX "PRESS & HOLD" OTOMATIK COZUCU
# ══════════════════════════════════════════════════════════════════════
# Duvar dump analizi (20260921_*_captcha.txt) gercek yapiyi gosterdi:
#
#   <div id="px-captcha-wrapper">
#     <div class="px-captcha-container">
#       <div id="px-captcha" style="display: block; min-width: 253px;">
#         <iframe style="display: none; ..." token="..." title="İnsan doğrulama sınaması">
#
# "Press & Hold" butonu IFRAME ICINDE. PX iframe'i once display:none yapar,
# sonra JS ile acar. Botun:
#   1) iframe'i bulmasi (display:none olsa bile DOM'da var),
#   2) iframe'e switch_to.frame ile girmesi,
#   3) icindeki butonu bulmasi (PX butonu genelde <div> — id/class PX'e ozgu),
#   4) CDP Input.dispatchMouseEvent ile GERCEK mousePressed → bekle →
#      mouseReleased simulasyonu yapmasi gerekir.
#
# NEDEN CDP (ActionChains degil)? ActionChains.click_and_hold() W3C Actions
# kullanir ve PX'in kendi JS'i bunu bazen "sentetik" olarak isaretler. CDP
# Input.dispatchMouseEvent ise tarayicinin EN ALT girdi katmanina iner —
# isTrusted=true, gercek fare olayindan ayirt edilemez. PX'in "basili tut"
# suresi olcumu (basma-birakma arasi ms) bu katmandan okunur.
#
# ONEMLI: PX basili-tut suresini olcer. Cok kisa (<1.5sn) = bot, cok uzun
# (>5sn) = takilma. PX_BASILI_TUT_MIN/MAX araliginda RASTGELE tutuyoruz.
# Ayrica basmadan once imleci butona "yuruyerek" goturuyoruz (isinlanma yok).


def _px_iframe_bul(driver):
    """PX challenge iframe'ini bul. display:none olsa bile DOM'da olabilir.

    21.09.2026 — SAHA DUMP'I (duvar_dump/20260921_144431_captcha.txt) ile
    GERCEK yapi dogrulandi:

        <div id="px-captcha-wrapper">
          <div class="px-captcha-container">
            <div id="px-captcha" style="display: block; min-width: 253px;">
              <iframe style="display: none; width: 100%; height: 52px; ..."
                      token="5079b24b..."
                      title="İnsan doğrulama sınaması"></iframe>

    ONEMLI: Onceki surumde Shadow DOM taramasindan JS ile donen node
    kullaniliyordu; Selenium bunu WebElement'e ceviremedigi icin
    switch_to.frame 'invalid argument: missing ELEMENT' hatasi veriyordu.
    Artik SADECE Selenium'un kendi buldugu gercek WebElement'ler doner.
    Ayrica iframe display:none ise PX'in kendi JS'i acar; biz de
    gorunurluk zorlamasi yapmiyoruz (PX bunu tespit eder).

    Doner: (element, index) | (None, None). index, switch_to.frame icin
    ASIL yol (element stale/serialize edilemezse index ile gireriz).

    21.09.2026 DUZELTME: Onceki surumde konteyner selector eslesince
    'return els[0], None' yapiliyordu — index None kaliyordu, bu yuzden
    switch_to.frame(element) 'missing ELEMENT' verince index fallback
    HIC CALISMIYORDU. Artik index HER ZAMAN hesaplanir: tum iframe'ler
    listesinde bu elementin sirasi bulunur."""
    # 1) PX konteyneri icindeki iframe (en guvenilir — PX'in kendi id'si)
    konteyner_sec = [
        "#px-captcha-wrapper iframe",
        "#px-captcha iframe",
        "[id*='px-captcha'] iframe",
        "[class*='px-captcha'] iframe",
    ]
    hedef = None
    for sel in konteyner_sec:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                hedef = els[0]
                break
        except Exception:
            continue
    # 2) Konteynerde yoksa: tum iframe'ler — nitelik taramasi
    if hedef is None:
        try:
            adaylar = driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            adaylar = []
        for fr in adaylar:
            try:
                baslik = (fr.get_attribute("title") or "").lower()
                src = (fr.get_attribute("src") or "").lower()
                token = fr.get_attribute("token") or ""
                isim = (fr.get_attribute("name") or "").lower()
                if ("insan" in baslik or "doğrulama" in baslik
                        or "dogrulama" in baslik
                        or "px-cloud.net" in src or "px-captcha" in src
                        or token or "px" in isim):
                    hedef = fr
                    break
            except Exception:
                continue
    if hedef is None:
        return None, None
    # 3) Index'i HER ZAMAN hesapla (switch_to.frame icin guvenilir yol).
    #    21.09.2026 — Selenium element karsilastirmasi (fr == hedef)
    #    GUVENILMEZ cikti; index None kaliyordu. Bunun yerine JS ile
    #    iframe'in DOM sirasini buluruz (document.querySelectorAll).
    idx = None
    try:
        idx = driver.execute_script("""
            var hedef = arguments[0];
            var tumu = document.querySelectorAll('iframe');
            for (var i = 0; i < tumu.length; i++) {
                if (tumu[i] === hedef) return i;
            }
            // element referansi eslesmediyse token/title ile esle
            var tok = hedef.getAttribute('token') || '';
            var bas = (hedef.getAttribute('title') || '').toLowerCase();
            for (var j = 0; j < tumu.length; j++) {
                var t2 = tumu[j].getAttribute('token') || '';
                var b2 = (tumu[j].getAttribute('title') || '').toLowerCase();
                if ((tok && t2 === tok) || (bas && b2 === bas)) return j;
            }
            return null;
        """, hedef)
    except Exception:
        idx = None
    if idx is not None:
        try:
            idx = int(idx)
        except Exception:
            idx = None
    return hedef, idx


def _px_dom_dump(driver, etiket="px_dom"):
    """21.09.2026 — TESHIS: PX challenge cozulemediginde GERCEK DOM'u diske
    yaz. Boylece iframe/buton secicileri sahadaki yapiya gore duzeltilebilir.
    Best-effort — hata olursa sessizce yutulur."""
    try:
        import datetime as _dt
        damga = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        yol = ROOT / f"{damga}_{etiket}.txt"
        parcalar = []
        parcalar.append("=== ANA SAYFA TITLE ===")
        try:
            parcalar.append(driver.title or "")
        except Exception:
            parcalar.append("(alimadi)")
        parcalar.append("\n=== IFRAME ENVANTERI ===")
        try:
            for i, fr in enumerate(driver.find_elements(By.TAG_NAME, "iframe")):
                try:
                    parcalar.append(
                        f"[{i}] title={fr.get_attribute('title')!r} "
                        f"src={fr.get_attribute('src')!r} "
                        f"token={fr.get_attribute('token')!r} "
                        f"name={fr.get_attribute('name')!r} "
                        f"id={fr.get_attribute('id')!r} "
                        f"class={fr.get_attribute('class')!r} "
                        f"gorunur={fr.is_displayed()}"
                    )
                except Exception as e:
                    parcalar.append(f"[{i}] (okunamadi: {e})")
        except Exception as e:
            parcalar.append(f"(iframe listesi alinamadi: {e})")
        parcalar.append("\n=== PX KONTEYNER HTML ===")
        try:
            for sel in ("#px-captcha-wrapper", "#px-captcha",
                        "[id*='px-captcha']", "[class*='px-captcha']"):
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    try:
                        parcalar.append(f"--- {sel} ---")
                        parcalar.append(el.get_attribute("outerHTML")[:4000])
                    except Exception:
                        pass
        except Exception as e:
            parcalar.append(f"(konteyner alinamadi: {e})")
        parcalar.append("\n=== SAYFA KAYNAGI (ilk 6000 karakter) ===")
        try:
            parcalar.append((driver.page_source or "")[:6000])
        except Exception:
            parcalar.append("(page_source alinamadi)")
        with open(yol, "w", encoding="utf-8") as f:
            f.write("\n".join(parcalar))
        log.info(f"[PX] DOM dump yazildi: {yol.name}")
    except Exception as e:
        log.warning(f"[PX] DOM dump yazilamadi: {e}")


def _px_buton_bul(driver):
    """iframe ICINDEKI 'Press & Hold' butonunu bul.

    21.09.2026 — SAHA DUMP'I ile dogrulanan gercek yapi: PX iframe'i
    display:none baslar; PX'in captcha.js'i iframe ICINE butonu enjekte
    eder ve iframe'i acar. Bu yuzden:
      - is_displayed() ZORUNLULUGU KALDIRILDI: iframe henuz acilmamisken
        buton DOM'da olabilir ama 'gorunmez' sayilir. Koordinat zaten
        getBoundingClientRect ile aliniyor (bkz. _px_basili_tut).
      - Once PX'e ozgu id/class, sonra role=button, sonra en buyuk
        tiklanabilir div (boyut > 0 olan).
    Doner: element|None."""
    # 1) PX'e ozgu id/class — gorunurluk sarti YOK
    seciciler = [
        "#px-captcha button",
        "#px-captcha div[role='button']",
        "#px-captcha",
        "[id*='px-captcha'] button",
        "[id*='px-captcha'] div[role='button']",
        "[id*='px-captcha']",
        "[class*='px-captcha'] button",
        "[class*='px-captcha'] div[role='button']",
        "div[role='button']",
        "button",
    ]
    for sel in seciciler:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                # boyutu olan (render edilmis) ilk elementi al
                try:
                    b = el.size
                    if b.get("width", 0) > 0 and b.get("height", 0) > 0:
                        return el
                except Exception:
                    continue
        except Exception:
            continue
    # 2) Son care: iframe icindeki en buyuk boyutlu div
    try:
        els = driver.find_elements(By.TAG_NAME, "div")
        boyutlu = []
        for e in els:
            try:
                b = e.size
                if b.get("width", 0) > 0 and b.get("height", 0) > 0:
                    boyutlu.append(e)
            except Exception:
                continue
        if boyutlu:
            return max(boyutlu,
                       key=lambda e: e.size.get("width", 0) * e.size.get("height", 0))
    except Exception:
        pass
    return None


def _px_basili_tut(driver, element):
    """CDP Input.dispatchMouseEvent ile GERCEK basili-tut simulasyonu.
    isTrusted=true, alt girdi katmani. Insan gibi: imleci butona yurut,
    bas, rastgele sure tut, birak.

    21.09.2026 — SAHA DUMP'I ile dogrulanan kritik nokta: PX iframe'i
    display:none baslar ve iframe ICINDEKI butonun getBoundingClientRect'i
    iframe'e GORE koordinat verir. Ama CDP Input.dispatchMouseEvent ANA
    frame viewport koordinati bekler. Bu yuzden iframe'in ana sayfadaki
    ofsetini (iframe.getBoundingClientRect) EKLEMEK ZORUNDAYIZ. Ayrica
    iframe display:none iken iframe rect'i 0 donebilir — bu durumda PX
    iframe'i henuz acmamis demektir; buton koordinati gecersiz sayilir ve
    ust katman (cagiran) tekrar dener."""
    # 0) iframe'in ANA SAYFADAKI ofsetini al (iframe icindeyiz, ama
    #    iframe'in kendi konumu ana frame'e gore lazim).
    iframe_ofset_x = 0
    iframe_ofset_y = 0
    try:
        ofs = driver.execute_script("""
            // iframe icindeyiz: window.frameElement ana sayfadaki iframe'i verir
            var fe = window.frameElement;
            if (!fe) return {x: 0, y: 0, w: 0, h: 0, gorunur: false};
            var r = fe.getBoundingClientRect();
            var st = window.parent.getComputedStyle(fe);
            return {x: r.left, y: r.top, w: r.width, h: r.height,
                    gorunur: (st.display !== 'none' && r.width > 0)};
        """)
        iframe_ofset_x = int(ofs.get("x", 0) or 0)
        iframe_ofset_y = int(ofs.get("y", 0) or 0)
        if not ofs.get("gorunur"):
            log.warning("[PX] iframe hala display:none — PX henuz acmadi, "
                        "kisa bekleme sonrasi tekrar denenecek")
            return False
    except Exception as e:
        log.warning(f"[PX] iframe ofseti alinamadi: {e}")
    # 1) Buton merkezini JS ile al (iframe-ici koordinat)
    try:
        rect = driver.execute_script("""
            const el = arguments[0];
            const r = el.getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        """, element)
        cx = int(rect["x"] + rect["w"] / 2) + iframe_ofset_x
        cy = int(rect["y"] + rect["h"] / 2) + iframe_ofset_y
    except Exception as e:
        log.warning(f"[PX] buton konumu (JS) alinamadi: {e}")
        # Fallback: Selenium location
        try:
            konum = element.location_once_scrolled_into_view
            boyut = element.size
            cx = int(konum["x"] + boyut["width"] / 2) + iframe_ofset_x
            cy = int(konum["y"] + boyut["height"] / 2) + iframe_ofset_y
        except Exception as e2:
            log.warning(f"[PX] buton konumu (fallback) alinamadi: {e2}")
            return False
    if cx <= 0 or cy <= 0:
        log.warning(f"[PX] buton koordinati gecersiz ({cx},{cy}) — "
                    f"iframe henuz acilmamis olabilir")
        return False

    # 1) Imleci butona YURUYEREK gotur (isinlanma yok — PX bunu olcer)
    try:
        baslangic_x = cx + random.randint(-180, 180)
        baslangic_y = cy + random.randint(-120, 120)
        adim = random.randint(6, 12)
        for i in range(1, adim + 1):
            ara_x = int(baslangic_x + (cx - baslangic_x) * i / adim)
            ara_y = int(baslangic_y + (cy - baslangic_y) * i / adim)
            driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": ara_x, "y": ara_y,
                "button": "none", "buttons": 0,
            })
            time.sleep(random.uniform(0.01, 0.04))
        time.sleep(_jitter(PX_HEDEFLEME_MIN, PX_HEDEFLEME_MAX))
    except Exception as e:
        log.warning(f"[PX] imlec yurutme basarisiz: {e}")

    # 2) Bas (mousePressed)
    try:
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": cx, "y": cy,
            "button": "left", "buttons": 1, "clickCount": 1,
        })
    except Exception as e:
        log.warning(f"[PX] mousePressed basarisiz: {e}")
        return False

    # 3) Basili tut — rastgele sure (insan 1.8-3.6 sn). Ara mousemove ile
    #    "titreme" ekle (gercek parmak/fare tam sabit durmaz).
    tut = _jitter(PX_BASILI_TUT_MIN, PX_BASILI_TUT_MAX)
    gecen = 0.0
    while gecen < tut:
        adim_sn = random.uniform(0.08, 0.2)
        time.sleep(adim_sn)
        gecen += adim_sn
        try:
            driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": cx + random.randint(-2, 2),
                "y": cy + random.randint(-2, 2),
                "button": "left", "buttons": 1,
            })
        except Exception:
            pass

    # 4) Birak (mouseReleased)
    try:
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": cx, "y": cy,
            "button": "left", "buttons": 0, "clickCount": 1,
        })
    except Exception as e:
        log.warning(f"[PX] mouseReleased basarisiz: {e}")
        return False
    log.info(f"[PX] basili-tut tamamlandi ({tut:.1f} sn)")
    return True


def _px_erisilebilirlik_coz(driver):
    """21.09.2026 — ALTERNATIF COZUM B: PX ERISILEBILIRLIK (Accessibility) MODU.

    KULLANICI SAHA TARIFI (birebir):
      "Basili tut kismi geliyor ve oldugu gibi duruyor orasi. Basili tutma
       tusunun hemen yaninda ERISILEBILIR DOGRULAMA BUTONU var; oraya bir kez
       bastiktan sonra kutucuk DOLMAYA basliyor, ortalama 5 saniye sonra dolan
       butona basiyorsun ve devam ediyor."

    Yani PX challenge'in IKI yolu var:
      (1) Press & Hold  → basili tut (bot bunu deniyor, PX sentetik buluyor)
      (2) Accessibility → butona bas → progress bar ~5sn dolar → dolan butona bas

    Erisilebilirlik yolu botlar icin COK DAHA KOLAY: basili-tut SURESI
    olculmez, sadece iki ayri TIKLAMA + arada bekleme gerekir. PX bu modu
    ekran okuyucu kullanicilari icin sundugu icin tiklama paternini daha
    az sorgular.

    Bu fonksiyon IFRAME ICINDE cagrilmalidir (cagiran switch_to.frame yapar).
    Doner: True (erisilebilirlik akisi denendi) / False (buton bulunamadi)."""
    # 1) Erisilebilirlik butonunu bul. PX bunu genelde aria-label / title /
    #    role=button ile isaretler; metni "Erisilebilir"/"Accessible" olabilir.
    #    Ayrica basili-tut butonunun YANINDA kucuk bir ikon olarak durur.
    seciciler = [
        "[aria-label*='risilebilir']", "[aria-label*='ccessib']",
        "[title*='risilebilir']", "[title*='ccessib']",
        "[aria-label*='dogrulama']", "[aria-label*='doğrulama']",
        "[aria-label*='verification']", "[aria-label*='verify']",
        "button[aria-label]", "[role='button'][aria-label]",
        "a[aria-label]", "img[alt*='risilebilir']", "img[alt*='ccessib']",
        "[class*='accessib']", "[id*='accessib']",
        "[class*='a11y']", "[id*='a11y']",
    ]
    hedef = None
    for sel in seciciler:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                try:
                    b = el.size
                    if b.get("width", 0) > 0 and b.get("height", 0) > 0:
                        hedef = el
                        log.info(f"[PX] erisilebilirlik butonu bulundu: {sel}")
                        break
                except Exception:
                    continue
            if hedef is not None:
                break
        except Exception:
            continue
    if hedef is None:
        # 2) Son care: iframe icindeki TUM tiklanabilir kucuk elemanlari tara,
        #    metni/aria'si erisilebilirlikle ilgili olani sec.
        try:
            adaylar = driver.find_elements(
                By.CSS_SELECTOR, "button, [role='button'], a, div[tabindex]")
        except Exception:
            adaylar = []
        for el in adaylar:
            try:
                metin = ((el.text or "") + " " +
                         (el.get_attribute("aria-label") or "") + " " +
                         (el.get_attribute("title") or "")).lower()
                if any(k in metin for k in
                       ("eriş", "eris", "accessib", "a11y", "doğrula",
                        "dogrula", "verify", "verification")):
                    b = el.size
                    if b.get("width", 0) > 0 and b.get("height", 0) > 0:
                        hedef = el
                        log.info("[PX] erisilebilirlik butonu (metin taramasi) "
                                 "bulundu")
                        break
            except Exception:
                continue
    if hedef is None:
        log.info("[PX] erisilebilirlik butonu bulunamadi")
        return False

    # 3) Butona GERCEK tiklama (CDP Input — isTrusted=true). Once imleci
    #    yuruyerek gotur, sonra bas-birak.
    try:
        rect = driver.execute_script("""
            const el = arguments[0];
            const r = el.getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        """, hedef)
        cx = int(rect["x"] + rect["w"] / 2)
        cy = int(rect["y"] + rect["h"] / 2)
    except Exception as e:
        log.warning(f"[PX] erisilebilirlik buton konumu alinamadi: {e}")
        return False
    if cx <= 0 or cy <= 0:
        log.warning(f"[PX] erisilebilirlik buton koordinati gecersiz ({cx},{cy})")
        return False
    # iframe ofseti (ana frame koordinati icin)
    try:
        ofs = driver.execute_script("""
            var fe = window.frameElement;
            if (!fe) return {x: 0, y: 0};
            var r = fe.getBoundingClientRect();
            return {x: r.left, y: r.top};
        """)
        cx += int(ofs.get("x", 0) or 0)
        cy += int(ofs.get("y", 0) or 0)
    except Exception:
        pass
    try:
        # imleci yuruyerek gotur
        bx = cx + random.randint(-120, 120)
        by = cy + random.randint(-80, 80)
        for i in range(1, random.randint(6, 10) + 1):
            ax = int(bx + (cx - bx) * i / 8)
            ay = int(by + (cy - by) * i / 8)
            driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": ax, "y": ay,
                "button": "none", "buttons": 0})
            time.sleep(random.uniform(0.01, 0.03))
        time.sleep(_jitter(0.3, 0.8))
        # bas
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": cx, "y": cy,
            "button": "left", "buttons": 1, "clickCount": 1})
        time.sleep(_jitter(0.08, 0.18))
        # birak
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": cx, "y": cy,
            "button": "left", "buttons": 0, "clickCount": 1})
        log.info("[PX] erisilebilirlik butonuna tiklandi — kutucuk dolmasi "
                 "bekleniyor (~5sn)")
    except Exception as e:
        log.warning(f"[PX] erisilebilirlik tiklamasi basarisiz: {e}")
        return False

    # 4) Progress bar'in DOLMASINI bekle (~5 sn) — kullanici tarifi: ortalama
    #    5 saniye. Sonra "dolan butona" bas. Dolan buton genelde AYNI konumda
    #    olur (buton icindeki progress tamamlaninca tiklanabilir hale gelir).
    time.sleep(_jitter(4.5, 6.0))
    try:
        # dolan butona tekrar tikla (ayni koordinat)
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": cx, "y": cy,
            "button": "left", "buttons": 1, "clickCount": 1})
        time.sleep(_jitter(0.08, 0.18))
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": cx, "y": cy,
            "button": "left", "buttons": 0, "clickCount": 1})
        log.info("[PX] dolan butona tiklandi (erisilebilirlik akisi tamam)")
    except Exception as e:
        log.warning(f"[PX] dolan buton tiklamasi basarisiz: {e}")
        return False
    return True


def _px_coz(driver, stop_event=None):
    """PerimeterX 'Press & Hold' challenge'ini OTOMATIK cozmeye calis.
    Doner: True (cozuldu) / False (cozulemedi — kullaniciya birakilir).

    Akis: iframe bul → icine gir → buton bul → basili tut → cik → sayfanin
    kendine gelmesini bekle → tekrar kontrol et. PX_MAX_DENEME kez dener."""
    if not PX_OTOMATIK_COZ:
        return False
    for deneme in range(1, PX_MAX_DENEME + 1):
        if stop_event is not None and stop_event.is_set():
            return False
        log.info(f"[PX] otomatik cozum denemesi {deneme}/{PX_MAX_DENEME}")
        try:
            # iframe'e gir
            # 21.09.2026 — SAHA BULGUSU (dump 20260921_150857): PX challenge
            # sayfasi yuklendiginde #px-captcha icinde ONCE 'px-loader'
            # (yukleniyor animasyonu) olur; captcha.js iframe'i SONRADAN
            # olusturur. Bu yuzden iframe yoksa HEMEN pes etme — loader
            # kaybolana / iframe olusana kadar bekle (en fazla ~15 sn).
            fr, fr_idx = None, None
            for _ifb in range(30):     # en fazla ~15 sn
                fr, fr_idx = _px_iframe_bul(driver)
                if fr is not None:
                    break
                # loader var mi? varsa PX hala hazirliyor → bekle
                try:
                    _loader = driver.execute_script("""
                        return !!document.querySelector(
                            '#px-captcha .px-loader-wrapper, '
                            + '#px-captcha .px-inner-loading-area, '
                            + '.px-loader-wrapper');
                    """)
                except Exception:
                    _loader = False
                if not _loader and _ifb >= 6:
                    # loader yok ve 3 sn gecti → iframe gelmeyecek
                    break
                time.sleep(0.5)
            if fr is None:
                log.warning("[PX] challenge iframe bulunamadi "
                            "(loader/iframe olusmadi)")
                # 21.09.2026 — TESHIS: ilk denemede gercek DOM'u diske yaz
                if deneme == 1:
                    _px_dom_dump(driver, "px_iframe_yok")
                time.sleep(2)
                continue
            # 21.09.2026 — KRITIK: PX iframe'i display:none baslar; PX'in
            # kendi JS'i acar. Selenium switch_to.frame GORUNMEZ iframe'e
            # giremez ('missing ELEMENT' / 'element not interactable').
            # Bu yuzden once iframe'in GORUNUR olmasini bekle (en fazla ~10 sn).
            _gorunur = False
            for _gv in range(20):
                try:
                    _gorunur = driver.execute_script("""
                        var fe = arguments[0];
                        if (!fe) return false;
                        var st = window.getComputedStyle(fe);
                        var r = fe.getBoundingClientRect();
                        return (st.display !== 'none' && st.visibility !== 'hidden'
                                && r.width > 0 && r.height > 0);
                    """, fr)
                except Exception:
                    _gorunur = False
                if _gorunur:
                    break
                time.sleep(0.5)
            if not _gorunur:
                log.warning("[PX] iframe hala display:none — PX acmadi, "
                            "kisa bekleme sonrasi tekrar denenecek")
                time.sleep(2)
                continue
            # switch_to.frame: ONCE INDEX (guvenilir yol), sonra element.
            _girdi = False
            if fr_idx is not None:
                try:
                    driver.switch_to.frame(fr_idx)
                    _girdi = True
                    log.info(f"[PX] iframe'e index ile girildi ({fr_idx})")
                except Exception as e:
                    log.warning(f"[PX] iframe'e girilemedi (index {fr_idx}): {e}")
            if not _girdi:
                try:
                    driver.switch_to.frame(fr)
                    _girdi = True
                    log.info("[PX] iframe'e element ile girildi")
                except Exception as e:
                    log.warning(f"[PX] iframe'e girilemedi (element): {e}")
            if not _girdi:
                time.sleep(2)
                continue
            # 21.09.2026 — PX iframe'i display:none baslatip JS ile acar.
            # captcha.js iframe ICINE butonu enjekte eder. Bu yuzden butonu
            # bulmak icin DAHA UZUN bekle (en fazla ~12 sn) — PX'in kendi
            # JS'i iframe'i acip butonu render etmesi zaman alir.
            buton = None
            for _bekle in range(24):   # en fazla ~12 sn
                buton = _px_buton_bul(driver)
                if buton is not None:
                    break
                time.sleep(0.5)
            if buton is None:
                log.warning("[PX] 'Press & Hold' butonu bulunamadi "
                            "(iframe icerigi yuklenmedi?)")
                # 21.09.2026 — TESHIS: iframe ICERIGINI diske yaz (buton
                # secicisi yanlissa gercek yapiyi buradan goruruz).
                if deneme == 1:
                    try:
                        _px_dom_dump(driver, "px_buton_yok_iframe_ici")
                    except Exception:
                        pass
                driver.switch_to.default_content()
                time.sleep(2)
                continue
            # 21.09.2026 — basili-tut: iframe display:none ise _px_basili_tut
            # False doner. Bu durumda iframe'in acilmasini bekleyip TEKRAR
            # dene (ayni deneme icinde, en fazla 6 kez / ~9 sn).
            basarili = False
            for _bt in range(6):
                basarili = _px_basili_tut(driver, buton)
                if basarili:
                    break
                time.sleep(1.5)
                buton = _px_buton_bul(driver)
                if buton is None:
                    break
            # 21.09.2026 — ALTERNATIF COZUM B: basili-tut BASARISIZ olduysa
            # (PX sentetik basili-tut'u reddetti) ERISILEBILIRLIK modunu dene.
            # Kullanici tarifi: basili tut'un yanindaki erisilebilirlik
            # butonuna bas → kutucuk ~5sn dolar → dolan butona bas → gecer.
            # Bu mod basili-tut SURESINI olcmedigi icin botlar icin cok daha
            # kolaydir. Ayni iframe icindeyiz — dogrudan cagir.
            if not basarili:
                log.info("[PX] basili-tut basarisiz — ERISILEBILIRLIK modu "
                         "deneniyor (ALTERNATIF COZUM B)")
                try:
                    _eris_ok = _px_erisilebilirlik_coz(driver)
                except Exception as e:
                    log.warning(f"[PX] erisilebilirlik akisi hata: {e}")
                    _eris_ok = False
                if _eris_ok:
                    basarili = True
            # iframe'den cik (her durumda!)
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            if not basarili:
                time.sleep(2)
                continue
            # PX'in dogrulamayi islemesi icin bekle
            time.sleep(PX_COZUM_SONRASI_BEKLE)
            # Cozuldu mu? Sayfa artik PX icermiyorsa basarili.
            try:
                html = driver.page_source
                title = driver.title or ""
            except Exception:
                html, title = "", ""
            if not _px_mi(html, title):
                log.info("[PX] ✓ challenge cozuldu (otomatik)")
                return True
            log.warning(f"[PX] deneme {deneme} basarisiz — hala challenge var")
        except Exception as e:
            log.warning(f"[PX] cozum sirasinda hata: {type(e).__name__}: {e}")
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
        time.sleep(_jitter(1.5, 3.0))
    log.warning(f"[PX] {PX_MAX_DENEME} denemede cozulemedi — kullaniciya birakiliyor")
    return False


# ─── SAYFA TARA ────────────────────────────────────────────
def _baglanti_koptu_mu(e):
    """23.09.2026 — Istisna, TARAYICI CEVAP VERMIYOR anlamina mi geliyor?

    Neden gerekli (olculdu): tarayici asildiginda tara_sayfa yolundaki HER
    katman kendi try/except'iyle hatayi YUTUP bir sonrakine geciyordu. Her
    katman ayni olu tarayiciya tekrar soruyor ve her biri
    DRIVER_CLIENT_TIMEOUT_SN kadar (35sn) bekliyordu:

        Page.navigate 35 + watchdog 75 + erken sinyal 35
        + erken title 35 + find_element 35  ≈ 215 sn

    Sahada olculen takilma suresi 226-243 sn — birebir bu zincir. Artik
    baglanti koptuysa ilk fark eden yeniden firlatiyor; ana dongu driver'i
    ~35sn'de yeniden kuruyor (eskiden ~230sn)."""
    m = f"{type(e).__name__}: {e}"
    return ("Read timed out" in m or "HTTPConnectionPool" in m
            or "Connection refused" in m or "Max retries exceeded" in m
            or "invalid session id" in m or "no such window" in m)


def _px_erken_sinyal(driver):
    """21.09.2026 — PX skorlamasi sayfa YUKLENIRKEN (ilk 2-3 sn) yapilir.
    Sayfa geldikten SONRA scroll yapmak PX icin GEC KALIR — skor coktan
    verilmistir. Bu fonksiyon driver.get()'ten HEMEN sonra cagrilir ve
    sayfa yuklenirken CDP Input ile GERCEK (isTrusted) fare hareketi +
    kucuk scroll uretir. Boylece PX'in "insan mi" skorlamasi sirasinda
    organik davranis telemetrisi gorur.

    NOT: Bu, _insan_gibi_davran()'in YERINE gecmez — o sayfa geldikten
    sonra okuma/scroll simulasyonu yapar. Bu ise YUKLEME SIRASINDA calisir.
    Ikisi birlikte tam bir oturum davranisi olusturur."""
    try:
        # Rastgele baslangic noktasindan birkac yumusak fare hareketi
        x, y = random.randint(200, 900), random.randint(150, 500)
        for _ in range(random.randint(3, 6)):
            x += random.randint(-120, 120)
            y += random.randint(-60, 60)
            x = max(10, min(x, 1300))
            y = max(10, min(y, 700))
            driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y,
                "button": "none", "buttons": 0,
            })
            time.sleep(random.uniform(0.03, 0.12))
        # Kucuk bir scroll (tekerlek) — sayfa yuklenirken dogal davranis
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": x, "y": y,
            "deltaX": 0, "deltaY": random.randint(80, 220),
        })
    except Exception as e:
        # Erken sinyal BEST-EFFORT — ama tarayici CEVAP VERMIYORSA devam
        # etmek anlamsiz: sonraki katmanlar da ayni olu tarayiciya sorup
        # her biri 35sn daha yakar (bkz. _baglanti_koptu_mu).
        if _baglanti_koptu_mu(e):
            raise
        pass


def _px_navigate_ile_sinyal(driver, url):
    """21.09.2026 — ALTERNATIF COZUM 2: PX skorlamasi sayfa YUKLENIRKEN
    (ilk 2-3 sn) yapilir. Eskiden driver.get() ile navigate edip SONRA
    _px_erken_sinyal() cagiriliyordu — ama o noktada PX skoru COKTAN
    verilmis oluyordu (gec kalma). Burada CDP Page.navigate ile navigate
    edip, navigate DEVAM EDERKEN (sayfa yuklenirken) fare hareketi +
    scroll uretiyoruz. Boylece PX'in skorlama penceresinde organik
    telemetri gorunur.

    Doner: True (navigate baslatildi) / False (CDP basarisiz — caller
    driver.get() fallback yapar)."""
    try:
        driver.execute_cdp_cmd("Page.navigate", {"url": url})
    except Exception as e:
        # 23.09.2026 — tarayici cevap vermiyorsa driver.get() fallback'i
        # denemek 75sn daha yakar, ayni sonucu verir. Hemen firlat.
        if _baglanti_koptu_mu(e):
            raise
        log.warning(f"[PX] Page.navigate basarisiz ({type(e).__name__}) — "
                    f"driver.get() fallback")
        return False
    # 23.09.2026 — _px_erken_sinyal() burada da DEVRE DISI. Asilmalarin
    # kanitlanmis kaynagi (bkz. tara_sayfa'daki ayrintili not). CDP yolu
    # ileride tekrar acilsa bile ayni tuzaga dusmeyelim.
    return True


# ══════════════════════════════════════════════════════════════════════
# 21.09.2026 — NAVIGATE WATCHDOG (KRITIK ASILMA DUZELTMESI).
# ══════════════════════════════════════════════════════════════════════
# SORUN: driver.get() ve CDP Page.navigate, proxy baglantisi ASILDIĞINDA
# (residential proxy bazen TCP baglantisini yarim birakir) Selenium'un
# page_load_timeout degerini YOK SAYAR ve SONSUZA KADAR bloklar. Bot
# tamamen donar (CPU %0, log durur) — kullanici mudahale etmeden kurtulmaz.
#
# COZUM: Navigate'i AYRI bir thread'de calistir. Belirli bir sure icinde
# donmezse: (1) log'a yaz, (2) driver'i ZORLA quit et (asılı HTTP cagrisi
# boylece sonlanir), (3) NAVIGATE_ASILDI istisnasi firlat. Cagiran taraf
# bunu yakalayip proxy degistirir. Boylece bot ASLA donmaz.
NAVIGATE_WATCHDOG_SN = int(os.getenv("NAVIGATE_WATCHDOG_SN", "75") or "75")


class NavigateAsildi(Exception):
    """Navigate watchdog suresi asildi — proxy/oturum asilmis demektir."""
    pass


def _arka_planda_kapat(driver):
    """driver.quit()'i arka plana at, DONMESINI BEKLEME.

    22.09.2026 — chromedriver cevap vermez hale geldiginde quit() de ayni
    asili HTTP kanalini kullanir ve 120sn bloklar. Kurtarma sirasinda bunu
    beklemek, kurtarmanin kendisini yavaslatan ana sebepti."""
    if driver is None:
        return

    def _isci():
        try:
            driver.quit()
        except Exception:
            pass

    threading.Thread(target=_isci, daemon=True).start()


def _watchdog_navigate(driver, url, timeout_sn):
    """driver.get(url)'i ayri thread'de calistir; timeout_sn icinde donmezse
    driver'i zorla kapat ve NavigateAsildi firlat. Doner: None (basarili)."""
    sonuc = {"hata": None, "bitti": False}

    def _isci():
        try:
            driver.get(url)
        except Exception as e:      # TimeoutException dahil — normal
            sonuc["hata"] = e
        finally:
            sonuc["bitti"] = True

    t = threading.Thread(target=_isci, daemon=True)
    t.start()
    t.join(timeout_sn)
    if not sonuc["bitti"]:
        # Navigate hala donmedi → ASILDI. driver'i zorla kapat ki asili
        # HTTP cagrisi sonlansin (aksi halde thread sonsuza kadar kalir).
        log.error(f"[NAV] driver.get() {timeout_sn}sn'de donmedi — ASILDI; "
                  f"driver zorla kapatiliyor (proxy degistirilecek)")
        try:
            driver.quit()
        except Exception:
            pass
        raise NavigateAsildi(f"navigate {timeout_sn}sn'de donmedi")
    # Basarili veya normal TimeoutException — ikisi de kabul (sayfa kismen
    # yuklenmis olabilir; WebDriverWait zaten bekleyecek).
    return sonuc["hata"]


def tara_sayfa(driver, url):
    """Doner: (ilanlar, soup, html, title). title _sayfa_durumu() icin.

    21.09.2026 — Navigate WATCHDOG ile sarildi: proxy asilirsa bot DONMAZ,
    NavigateAsildi firlatilir (cagiran proxy degistirir)."""
    # 21.09.2026 — ALTERNATIF COZUM 2: once CDP Page.navigate + yukleme
    # SIRASINDA erken sinyal. CDP calismazsa driver.get() fallback.
    # 23.09.2026 — varsayilan artik DUZ driver.get() (bkz. PX_CDP_NAVIGATE).
    # Asilmalarin tamami CDP navigate yolunda olcüldu; driver.get() hem
    # set_page_load_timeout'a uyar hem de watchdog ile sarili.
    if not (PX_CDP_NAVIGATE and _px_navigate_ile_sinyal(driver, url)):
        _watchdog_navigate(driver, url, NAVIGATE_WATCHDOG_SN)
        # 23.09.2026 — _px_erken_sinyal() ARTIK CAGRILMIYOR.
        #
        # KANIT: koda konan "asildigi yer" kaydi, yakalanan iki asilmanin
        # IKISINDE de tam su satiri gosterdi:
        #   _px_erken_sinyal() → driver.execute_cdp_cmd("Input.dispatchMouseEvent")
        # Sayfa yuklenirken renderer'a fare olayi gondermek, renderer mesgulse
        # cevapsiz kaliyor → komut asiliyor → driver yeniden kuruluyor →
        # YENI OTURUM → PX challenge. Bugun olculdu: PX olaylarinin %100'u
        # tarayici acilisindan sonra geliyor. Yani bu fonksiyon, onlemeye
        # calistigi seyi URETIYORDU.
        #
        # Faydasi ayrica hic kanitlanmadi (_px_coz ile ayni kategori: o da
        # 33 denemede 0 basari ile kaldirildi). Fonksiyon dosyada duruyor,
        # cagrilmiyor.
    # 23.09.2026 — ERKEN TITLE KONTROLU (asilma duzeltmesi).
    #
    # KANIT (log stacktrace'i): asilma page_source'ta degil, ASAGIDAKI
    # WebDriverWait → find_element cagrisindaydi. Sayfa yuklenmesi bitmemisse
    # (PX challenge script'i / yarim kalan proxy baglantisi) chromedriver DOM
    # sorgusunu calistirmadan once bekliyor; WebDriverWait'in 10sn'lik siniri
    # TEK BIR cagrinin icini baglamadigi icin ise yaramiyor. Olculen sonuc:
    # sayfa isteginden ~95sn sonra client timeout → driver restart → ve
    # restart'larin %20'si kullaniciya PX cikariyordu.
    #
    # COZUM: once title'i oku (ucuz komut, DOM sorgusu degil). Sayfa gercek
    # bir liste sayfasi DEGILSE (PX/CF/giris) ne element beklemeye ne de
    # ~30-60sn'lik insan simulasyonuna gerek var — durumu hemen dondur,
    # ana dongu dogru dala gitsin ve kullanici ekrani elle gecsin.
    try:
        on_title = driver.title or ""
    except Exception as e:
        # 23.09.2026 — bu kontrolu dun "ucuz" diye ekledim ama hatayi
        # yutuyordu; asili tarayicida zincire 35sn daha ekliyordu.
        if _baglanti_koptu_mu(e):
            raise
        on_title = ""
    if on_title and (_cf_title_mi(on_title) or _px_title_mi(on_title)):
        log.info(f"  [ERKEN] engel ekrani title'dan taninidi ({on_title[:60]!r}) "
                 f"— element beklemesi ve insan simulasyonu ATLANDI")
        try:
            html = driver.page_source
        except Exception:
            html = ""
        return [], None, html, on_title
    try:
        WebDriverWait(driver, RENDER_BEKLEME_TAVANI).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "tr.searchResultsItem"))
        )
    except TimeoutException:
        pass
    time.sleep(_jitter(RENDER_SETTLE_MIN, RENDER_SETTLE_MAX))
    _insan_gibi_davran(driver)
    html = driver.page_source
    try:
        title = driver.title or ""
    except Exception:
        title = ""
    ilanlar, soup = parse_ilanlar(html)
    return ilanlar, soup, html, title


# ═══════════════════════════════════════════════════════════
# FAZ 3 — DETAY SAYFA CEKIMI (fetch + worker thread)
# ═══════════════════════════════════════════════════════════

# ─── FAZ 3: DETAY PARSER ───────────────────────────────────
_INFO_KEY_MAP = {
    "yakıt / motor tipi": "yakit",
    "vites": "vites",
    "renk": "renk",
    "kasa tipi": "kasa",
    "motor gücü": "motor_gucu",
    "motor hacmi": "motor_cc",
    "çekiş": "cekis",
    "takas": "takas",
    "servis garantisi": "servis_garantisi",
    "ağır hasar kayıtlı": "agir_hasar_str",
    "plaka / uyruk": "plaka_uyruk",
}

_RE_INT_ANY   = re.compile(r"(\d+)")
_RE_GPS       = re.compile(r"maps/dir/Current\+Location/([\d.\-]+),([\d.\-]+)")
# Kirmizi bayrak — aciklama regex'leri
_RE_KIRMIZI = {
    "senetli":       re.compile(r"\b(senetli|vadeli|kefil|taksitli|taksit)\b", re.IGNORECASE),
    "hasar_beyani":  re.compile(r"\b(hasarlı|hasarli|pert|kaza|ağır hasar|agir hasar)\b", re.IGNORECASE),
    "mekanik":       re.compile(r"\b(motor arıza|motor ariza|şanzıman|sanziman|yürümüyor|yurumuyor)\b", re.IGNORECASE),
}


def _text_or_empty(el):
    if not el:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True))


def parse_detay_html(html, ilan_yil=None, ilan_km=None, ilan_baslik="", ilan_model=""):
    """Sahibinden detay sayfasi HTML'ini parse et. dict doner."""
    soup = BeautifulSoup(html, "html.parser")
    d = {
        "orijinal_mi": 0, "boyali": 0, "lokal_boyali": 0, "degisen": 0,
        "hasar_puani": 0.0,
        "agir_hasar": None, "plaka_uyruk": "", "yakit": "", "vites": "",
        "renk": "", "kasa": "", "motor_gucu": None, "motor_cc": None,
        "motor_hacim_kesin": None, "cekis": "", "takas": "",
        "servis_garantisi": "", "lat": None, "lon": None,
        "foto_sayisi": 0, "aciklama_uzunluk": 0, "aciklama_ozet": "",
        "ilan_kalite_skoru": 0.0, "kirmizi_bayrak": [],
        "satici_yil": "", "yetki_belge": "",
    }

    # 1) Yapilandirilmis alanlar: ul.classifiedInfoList > li
    for li in soup.select("ul.classifiedInfoList > li"):
        key_el = li.find("strong")
        val_el = li.find("span")
        if not (key_el and val_el):
            continue
        key = _text_or_empty(key_el).lower().rstrip(":")
        val = _text_or_empty(val_el)
        mapped = _INFO_KEY_MAP.get(key)
        if not mapped:
            continue
        if mapped in ("motor_gucu", "motor_cc"):
            m = _RE_INT_ANY.search(val)
            if m:
                d[mapped] = int(m.group(1))
        elif mapped == "agir_hasar_str":
            d["agir_hasar"] = 1 if val.strip().lower().startswith("evet") else 0
        else:
            d[mapped] = val

    # motor_cc → motor_hacim_kesin (1248 → 1.2)
    if d["motor_cc"] and d["motor_cc"] >= 400:
        d["motor_hacim_kesin"] = round(d["motor_cc"] / 1000.0, 1)

    # 2) Boya-degisen (car-damage-info-list)
    orijinal_found = False
    for pair in soup.select("ul li.pair-title"):
        classes = pair.get("class", [])
        # sirali kardesler icinde selected-damage li'lerini say
        sayi = 0
        for sibling in pair.find_next_siblings("li"):
            if "pair-title" in (sibling.get("class") or []):
                break
            if "selected-damage" in (sibling.get("class") or []):
                sayi += 1
        if "other-pair" in classes:
            # "Aracin tum parcalari orijinaldir" tek li
            orijinal_found = True
        elif "painted-new" in classes:
            d["boyali"] += sayi
        elif "changed-new" in classes:
            d["degisen"] += sayi
        elif "local-painted-new" in classes:
            d["lokal_boyali"] += sayi

    if orijinal_found and (d["boyali"] + d["degisen"] + d["lokal_boyali"]) == 0:
        d["orijinal_mi"] = 1
        d["hasar_puani"] = 0.0
    else:
        d["hasar_puani"] = (d["boyali"] * 1.0 + d["lokal_boyali"] * 0.5
                            + d["degisen"] * 3.0)

    # 3) Aciklama
    aciklama_el = soup.select_one("#classifiedDescription")
    aciklama_txt = _text_or_empty(aciklama_el) if aciklama_el else ""
    # Aciklama basligini (Aciklama, Ozellikler tab) ayikla
    aciklama_txt = re.sub(r"^\s*Açıklama\s*", "", aciklama_txt)
    d["aciklama_uzunluk"] = len(aciklama_txt)
    d["aciklama_ozet"] = aciklama_txt[:500]

    # 4) Foto sayisi
    d["foto_sayisi"] = len(soup.select("img.thmbImg"))

    # 5) GPS koordinati
    for a in soup.select("a.map-detail-directions-link, a[href*='maps/dir/']"):
        href = a.get("href", "")
        m = _RE_GPS.search(href)
        if m:
            try:
                d["lat"] = float(m.group(1))
                d["lon"] = float(m.group(2))
                break
            except ValueError:
                pass

    # 6) Satici yil + yetki belge
    yil_el = soup.select_one(".user-info-module .year")
    if yil_el:
        yil_txt = _text_or_empty(yil_el)
        min_el = soup.select_one(".user-info-module .min")
        if min_el:
            d["satici_yil"] = f"{yil_txt} {_text_or_empty(min_el)}"
        else:
            d["satici_yil"] = yil_txt
    ybl_el = soup.select_one(".user-info-license-id-value")
    if ybl_el:
        d["yetki_belge"] = _text_or_empty(ybl_el)

    # 7) Kirmizi bayrak
    bayraklar = []
    if d["agir_hasar"] == 1:
        bayraklar.append("agir_hasar")
    if d["degisen"] >= 4:
        bayraklar.append("cok_degisen")
    if d["plaka_uyruk"] and "türkiye" not in d["plaka_uyruk"].lower() and "tr" not in d["plaka_uyruk"].lower()[:15]:
        bayraklar.append("yabanci_plaka")
    if "gümrüksüz" in d["plaka_uyruk"].lower() or "yurt dışı" in d["plaka_uyruk"].lower():
        bayraklar.append("yabanci_plaka")
    for etiket, rgx in _RE_KIRMIZI.items():
        if rgx.search(aciklama_txt):
            bayraklar.append(etiket)
    if ilan_yil and ilan_km is not None:
        yil_fark = max(1, datetime.now().year - ilan_yil)
        if ilan_km < yil_fark * 5000:
            bayraklar.append("km_supheli")
    d["kirmizi_bayrak"] = bayraklar

    # 8) Ilan kalite skoru (0-100, DUSUK = BIZIM ICIN IYI)
    skor = 0.0
    if d["foto_sayisi"] < 5:      skor += 30
    elif d["foto_sayisi"] < 10:   skor += 15
    if d["aciklama_uzunluk"] < 50:      skor += 30
    elif d["aciklama_uzunluk"] < 200:   skor += 15
    # Baslikta model adi geciyor mu (basit kontrol)
    if ilan_model:
        model_words = [w for w in ilan_model.lower().split() if len(w) >= 3]
        if not any(w in (ilan_baslik or "").lower() for w in model_words):
            skor += 20
    # Aciklama bos veya tek satir
    if d["aciklama_uzunluk"] < 20:
        skor += 20
    d["ilan_kalite_skoru"] = min(100.0, skor)

    return d


# ─── FAZ 3: DB YAZ + GERI-BESLEME ──────────────────────────
def detay_yaz(con, ilan_id, d):
    """detay tablosuna INSERT/REPLACE. Aynı zamanda ilan tablosunda motor_hacim
    kesin degeri varsa günceller (parser tahminini gercek deger ezer)."""
    now = _now_iso()
    con.execute("""
        INSERT OR REPLACE INTO detay (
            ilan_id, orijinal_mi, boyali, lokal_boyali, degisen, hasar_puani,
            agir_hasar, plaka_uyruk, yakit, vites, renk, kasa,
            motor_gucu, motor_cc, motor_hacim_kesin, cekis, takas, servis_garantisi,
            lat, lon, foto_sayisi, aciklama_uzunluk, aciklama_ozet,
            ilan_kalite_skoru, kirmizi_bayrak,
            satici_yil, yetki_belge, okundu_tarih
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        ilan_id, d["orijinal_mi"], d["boyali"], d["lokal_boyali"], d["degisen"], d["hasar_puani"],
        d["agir_hasar"], d["plaka_uyruk"], d["yakit"], d["vites"], d["renk"], d["kasa"],
        d["motor_gucu"], d["motor_cc"], d["motor_hacim_kesin"], d["cekis"], d["takas"], d["servis_garantisi"],
        d["lat"], d["lon"], d["foto_sayisi"], d["aciklama_uzunluk"], d["aciklama_ozet"],
        d["ilan_kalite_skoru"], json.dumps(d["kirmizi_bayrak"], ensure_ascii=False),
        d["satici_yil"], d["yetki_belge"], now
    ))

    # ilan tablosunda yakit/vites/renk boşsa doldur (Faz 1 default eksik)
    con.execute("""
        UPDATE ilan SET yakit=COALESCE(NULLIF(yakit,''), ?),
                        vites=COALESCE(NULLIF(vites,''), ?),
                        renk =COALESCE(NULLIF(renk, ''), ?)
        WHERE ilan_id=?
    """, (d["yakit"], d["vites"], d["renk"], ilan_id))

    # motor_hacim_kesin varsa ilan.motor_hacim'i EZ ve bucket'i yeniden hesapla
    bucket_yenile = False
    if d["motor_hacim_kesin"]:
        row = con.execute(
            "SELECT motor_hacim, motor_hacim_grup, marka, seri, motor_tipi, paket, "
            "kasa_ipucu, yil, arac_sinifi FROM ilan WHERE ilan_id=?", (ilan_id,)
        ).fetchone()
        if row:
            eski_hacim, eski_hacim_grup, marka, seri, mtip, pkt, kasa, yil, sinif = row
            yeni_hacim_grup = motor_hacim_grup_hesapla(d["motor_hacim_kesin"])
            if eski_hacim != d["motor_hacim_kesin"]:
                con.execute(
                    "UPDATE ilan SET motor_hacim=?, motor_hacim_grup=? WHERE ilan_id=?",
                    (d["motor_hacim_kesin"], yeni_hacim_grup, ilan_id)
                )
                # Bucket'lari yeniden hesapla (hem eski hem yeni hacim_grup icin)
                if marka and seri and yil and sinif:
                    _bucket_yaz(con, "L2", marka, seri, yeni_hacim_grup, mtip, None, yil, sinif)
                    _bucket_yaz(con, "L1", marka, seri, yeni_hacim_grup, mtip, pkt, yil, sinif, kasa)
                    # Eski hacim_grup bucket'i icin de yeniden hesapla (bir ilan azaldi)
                    if eski_hacim_grup and eski_hacim_grup != yeni_hacim_grup:
                        _bucket_yaz(con, "L2", marka, seri, eski_hacim_grup, mtip, None, yil, sinif)
                        _bucket_yaz(con, "L1", marka, seri, eski_hacim_grup, mtip, pkt, yil, sinif, kasa)
                bucket_yenile = True
    return bucket_yenile


# ─── FAZ 3: WORKER + KUYRUK ────────────────────────────────
_DETAY_KUYRUK = queue.Queue(maxsize=DETAY_KUYRUK_MAX)
_DETAY_KUYRUK_MAX_DERINLIK = 0
_DETAY_STAT = {"islenen": 0, "basarili": 0, "hata": 0, "atlanan": 0,
               "toplam_sure": 0.0, "kirmizi_bayrakli": 0, "kalite_toplam": 0.0}
_DETAY_LOCK = threading.Lock()


# 17.09.2026 — KOK NEDEN: execute_script(fetch()) HER ISTEKTE block yiyordu
# (islenen=N, basarili=0 — sistematik, rastgele degil). fetch() API'si
# 'Sec-Fetch-Dest: empty' header'i tasir (JS/AJAX istegi imzasi), driver.get()
# ise 'Sec-Fetch-Dest: document' (gercek sayfa navigasyonu). Ana tarama
# driver.get() kullandigi icin block yemiyor artik (bkz. CLAUDE.md kok neden
# analizi) — detay worker'i da AYNI navigasyon yontemine cevirdik.
#
# TEK DRIVER paylasildigi icin (ana tarama + worker), gercek navigasyon
# SENKRONIZASYON gerektirir: iki thread ayni anda farkli sayfalara
# navigate edemez. Worker kendi AYRI SEKMESINDE calisir, driver_lock ile
# ana taramayla sirali erisim saglanir (paralellik azalir ama block riski
# neredeyse sifira iner).
def _detay_fetch_navigasyon(driver, url, driver_lock, sekme_ref):
    """driver.get() ile GERCEK navigasyon — ayri sekmede, lock ile senkron.
    Donus: (html, title) basarili ise, (None, None) hata/exception durumunda."""
    with driver_lock:
        try:
            handle = sekme_ref.get("handle")
            if handle and handle in driver.window_handles:
                driver.switch_to.window(handle)
            else:
                driver.switch_to.new_window("tab")
                sekme_ref["handle"] = driver.current_window_handle

            driver.get(url)
            try:
                WebDriverWait(driver, RENDER_BEKLEME_TAVANI).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "ul.classifiedInfoList"))
                )
            except TimeoutException:
                pass
            time.sleep(_jitter(RENDER_SETTLE_MIN, RENDER_SETTLE_MAX))
            html = driver.page_source
            try:
                title = driver.title or ""
            except Exception:
                title = ""
            return html, title
        except Exception:
            return None, None


def _takip_kontrol(driver, con, driver_lock, stop_event):
    """FAZ 2.5 — takipteki ilanlar hala yayinda mi?

    Kohort dengesi icin kuyruk 1 aday + 1 kontrol dondurur. Block gorulurse
    HEMEN durulur ve sonuc 'belirsiz' yazilir — block'u "satildi" saymak tum
    olcumu coper (bkz. takip.py docstring)."""
    try:
        import takip
    except Exception as e:
        log.warning(f"  [TAKIP] modul yuklenemedi: {e}")
        return

    try:
        kuyruk = takip.kontrol_kuyrugu(con, tavan=2)
    except Exception as e:
        log.warning(f"  [TAKIP] kuyruk alinamadi: {e}")
        return
    if not kuyruk:
        return

    canli = gitti = belirsiz = 0
    son_index = len(kuyruk) - 1
    for sira, (iid, kohort, url) in enumerate(kuyruk):
        if stop_event.is_set():
            break
        if not url:
            continue
        html, title = _detay_fetch_navigasyon(driver, url, driver_lock, _TAKIP_SEKME)
        durum = takip.sayfa_durumu(html, title)
        takip.sonuc_yaz(con, iid, durum)
        if durum == "gitti":
            gitti += 1
            log.info(f"  [TAKIP] {kohort} {iid} → YAYINDAN KALKTI")
        elif durum == "canli":
            canli += 1
        else:
            belirsiz += 1
            if durum == "block":
                log.warning("  [TAKIP] block sinyali — kontrol durduruldu "
                            "(sonuc sayima GIRMEDI)")
                break
        # SON istekten sonra bekleme YOK — molanin kendi tabani
        # (TAKIP_MOLA_TABANI) zaten o sessizligi sagliyor. Buraya bir
        # bekleme daha koymak molayi tasirip tur periyodunu uzatiyordu.
        if sira < son_index:
            time.sleep(_jitter(DETAY_FETCH_MIN, DETAY_FETCH_MAX))

    if canli or gitti or belirsiz:
        log.info(f"  [TAKIP] kontrol: canli={canli} gitti={gitti} "
                 f"belirsiz={belirsiz}")


def _takip_kohort_ekle(con):
    """Yeni adaylari + eslestirilmis kontrollerini takibe alir. Ag kullanmaz."""
    try:
        import kelepir
        import takip
        rows, modeller, skorlar, adaylar = kelepir.calistir()
        a, k = takip.kohort_ekle(con, adaylar, skorlar, TAKIP_KOHORT_TAVAN)
        if a or k:
            log.info(f"  [TAKIP] kohorta eklendi: {a} aday + {k} kontrol  "
                     f"(havuzda {len(adaylar)} aday)")
    except Exception as e:
        log.warning(f"  [TAKIP] kohort eklenemedi: {e}")


def _detay_worker(driver_ref, con_lock, stop_event, cf_block_event, driver_lock):
    """Kuyruktan aday cek, detay HTML fetch et, parse et, DB'ye yaz.
    Block detection + backoff: 3 ust uste block → 30dk mola, 5 → 2 saat.
    Sahibinden'in nefes almasi icin; bloklu isteklere devam etmeyiz.

    cf_block_event: ana dongu CF/block/bos tespit ederse set eder — worker
    bu sinyali gorunce kuyruktan CEKMEZ, iki cepheden ayni anda istek atip
    durumu kotulestirmez.

    driver_lock: TEK driver paylasildigi icin (driver.get() navigasyonu),
    ana tarama ile worker'in sirali erismesini saglar (bkz. _detay_fetch_navigasyon)."""
    con_worker = sqlite3.connect(DB_FILE, timeout=30.0)
    con_worker.execute("PRAGMA journal_mode=WAL")
    log.info("[FAZ3] detay worker basladi")
    ardarda_block = 0  # ust uste block sayaci
    sekme_ref = {"handle": None}   # worker'in kendi sekmesi (local, thread-safe)

    while not stop_event.is_set():
        if cf_block_event.is_set():
            # Ana dongu CF/block ile ugrasiyor — worker sussun, ek istek atma
            time.sleep(5)
            continue
        try:
            item = _DETAY_KUYRUK.get(timeout=5)
        except queue.Empty:
            continue
        if item is None:
            break
        oncelik, ilan_id, url = item
        fetch_yapildi = False   # sadece gercek istek atildiysa bekle
        try:
            # Zaten var mi kontrol
            exists = con_worker.execute(
                "SELECT 1 FROM detay WHERE ilan_id=?", (ilan_id,)
            ).fetchone()
            if exists:
                with _DETAY_LOCK:
                    _DETAY_STAT["atlanan"] += 1
                continue

            drv = driver_ref.get("driver")
            if drv is None:
                time.sleep(2)
                continue

            # Ilan meta
            meta = con_worker.execute(
                "SELECT yil, km, baslik, model FROM ilan WHERE ilan_id=?",
                (ilan_id,)
            ).fetchone()
            yil, km, baslik, model = meta if meta else (None, None, "", "")

            t0 = time.monotonic()
            html = None
            title = ""
            fetch_yapildi = True
            for r in range(DETAY_RETRY_MAX + 1):
                html, title = _detay_fetch_navigasyon(drv, url, driver_lock, sekme_ref)
                d_durum = _detay_sayfa_durumu(html, title) if html else "block"
                if d_durum == "ok":
                    break
                # 21.09.2026 — Detay sayfasinda PX HARD BLOCK (IP karalistesi).
                # Cozulemez — worker proxy degistiremez (ana driver'i paylasir).
                # Ana donguye sinyal ver (cf_block_event) ki o da dursun; worker
                # bu ilani atlar, ana dongu bir sonraki turda proxy failover
                # yapar. Bosuna _px_coz denemesi yapmaya gerek yok.
                if d_durum == "px_hard":
                    log.warning("[FAZ3] 🚫 detay sayfasinda PX HARD BLOCK "
                                "(IP karalistesi) — ilan atlaniyor, ana dongu "
                                "proxy failover yapacak")
                    cf_block_event.set()
                    html = None
                    break
                # 22.09.2026 — Detay sayfasinda PX challenge. Otomatik cozum
                # DEVRE DISI (33 deneme / 0 basari). Worker zaten
                # DETAY_WORKER_AKTIF=False ile kapali; acildiginda da ana
                # dongu gibi kullaniciya birakilmali, bosuna denenmemeli.
                if d_durum == "captcha":
                    log.warning("[FAZ3] 🖐️ detay sayfasinda PX challenge — "
                                "ilan atlaniyor (otomatik cozum kapali)")
                    cf_block_event.set()
                html = None
                time.sleep(_jitter(2.0, 4.0))

            if not html:
                # BLOCK/CF sayilir — sahibinden 50KB alti icerik veya CF challenge dondurdu
                ardarda_block += 1
                with _DETAY_LOCK:
                    _DETAY_STAT["hata"] += 1
                    _DETAY_STAT["islenen"] += 1

                if ardarda_block >= DETAY_BLOCK_UZUN_ESIK:
                    mola = DETAY_BLOCK_UZUN_SN
                    log.warning(f"[FAZ3] ⛔ {ardarda_block} ust uste block → "
                                f"UZUN mola {mola//60} dk (2 saat)")
                elif ardarda_block >= DETAY_BLOCK_KISA_ESIK:
                    mola = DETAY_BLOCK_KISA_SN
                    log.warning(f"[FAZ3] ⚠️ {ardarda_block} ust uste block → "
                                f"KISA mola {mola//60} dk (30 dk)")
                else:
                    mola = 0
                if mola:
                    # Ana donguye de haber ver — iki cephe ayni anda istek atmasin
                    cf_block_event.set()
                    end = time.monotonic() + mola
                    while time.monotonic() < end and not stop_event.is_set():
                        time.sleep(5)
                    cf_block_event.clear()
                    ardarda_block = 0  # mola sonrasi sifirla
                continue

            # BASARILI — sayacI sifirla
            ardarda_block = 0

            d = parse_detay_html(html, ilan_yil=yil, ilan_km=km,
                                 ilan_baslik=baslik, ilan_model=model)
            with con_lock:
                detay_yaz(con_worker, ilan_id, d)
                con_worker.commit()

            gecen = time.monotonic() - t0
            with _DETAY_LOCK:
                _DETAY_STAT["islenen"] += 1
                _DETAY_STAT["basarili"] += 1
                _DETAY_STAT["toplam_sure"] += gecen
                _DETAY_STAT["kalite_toplam"] += d["ilan_kalite_skoru"]
                if d["kirmizi_bayrak"]:
                    _DETAY_STAT["kirmizi_bayrakli"] += 1

        except Exception as e:
            log.warning(f"[FAZ3] worker hata ({ilan_id}): {e}")
            with _DETAY_LOCK:
                _DETAY_STAT["hata"] += 1
                _DETAY_STAT["islenen"] += 1
        finally:
            # task_done SADECE burada — continue'lardan cagirilmaz (cift cagri bug'i)
            try: _DETAY_KUYRUK.task_done()
            except ValueError: pass
            # Bekleme sadece gercek istek atildiysa (atlanan/driver-yok durumunda bekleme)
            if fetch_yapildi:
                time.sleep(_jitter(DETAY_FETCH_MIN, DETAY_FETCH_MAX))

    con_worker.close()
    log.info("[FAZ3] detay worker durdu")


# ─── FAZ 3: ADAY SECIMI ────────────────────────────────────
def aday_uret(con):
    """SADECE P1: n>=8 L1 bucket'ta, fiyat medyanin %20 altinda, detay yok.
    P2/P3 kapatildi — soft-block riski yerine sadece muhtemel kelepire bakiyoruz."""
    global _DETAY_KUYRUK_MAX_DERINLIK

    p1 = con.execute("""
        SELECT i.ilan_id, i.url FROM ilan i
        JOIN bucket_stat b ON b.marka=i.marka AND b.seri=i.seri
             AND b.motor_hacim=i.motor_hacim AND b.motor_tipi=i.motor_tipi
             AND b.paket=i.paket AND b.yil=i.yil AND b.arac_sinifi=i.arac_sinifi
             AND b.katman='L1'
        LEFT JOIN detay d ON d.ilan_id=i.ilan_id
        WHERE d.ilan_id IS NULL
          AND b.ilan_sayisi >= ?
          AND i.fiyat < b.medyan_fiyat * (1 - ?)
          AND i.durum='aktif'
        ORDER BY (b.medyan_fiyat - i.fiyat) / b.medyan_fiyat DESC
        LIMIT ?
    """, (DETAY_MIN_BUCKET_N, DETAY_ADAY_ESIK, DETAY_ADAY_PER_TUR)).fetchall()

    eklendi = 0
    for iid, url in p1:
        try:
            _DETAY_KUYRUK.put_nowait((1, iid, url))
            eklendi += 1
        except queue.Full:
            break

    d = _DETAY_KUYRUK.qsize()
    if d > _DETAY_KUYRUK_MAX_DERINLIK:
        _DETAY_KUYRUK_MAX_DERINLIK = d
    return len(p1), 0, 0, eklendi


# ─── ANA DONGU ─────────────────────────────────────────────
_KILIT_DOSYA = ROOT / "oto_bot.lock"
_kilit_fh = None


def _tek_instance_al():
    """20.09.2026 — ayni anda IKINCI bir bot calismasini engelle.

    Gozlenen ariza: iki instance ayni KALICI profili (brave_oto_profile_v2)
    acmaya calisinca Brave oturumu coktu ('invalid session id' /
    'no such window'), bot 17 tur boyunca bos dondu ve SIFIR veri topladi.
    Kilit process omru boyunca tutulur, surec olunce OS otomatik birakir
    (stale kilit sorunu yok).

    Doner: True (kilit alindi) / False (baska bot calisiyor)."""
    global _kilit_fh
    try:
        import msvcrt
    except ImportError:
        return True          # Windows disi ortam — kilit uygulanmaz
    try:
        _kilit_fh = open(_KILIT_DOSYA, "a+")
        msvcrt.locking(_kilit_fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if _kilit_fh is not None:
            try: _kilit_fh.close()
            except Exception: pass
            _kilit_fh = None
        return False
    try:
        _kilit_fh.seek(0)
        _kilit_fh.truncate()
        _kilit_fh.write(f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}\n")
        _kilit_fh.flush()
    except Exception:
        pass                 # kilit tutuldu; icerik yazamamak kritik degil
    return True


def main():
    log.info("=" * 60)
    log.info("  OTO KELEPIR AVCISI — Faz 1 Toplayici (v3)")
    log.info(f"  URL     : {OTO_URL_BASE}")
    log.info(f"  Derinlik: {SAYFA_DERINLIK_PLANI} (offset, her-N-turda-bir) — "
             f"kademeli, sira karisik (kok neden analizi 17.09)")
    _profil_adi = (LOGIN_PROFIL_DIR.name if LOGIN_PROFIL_KULLAN
                   else _BRAVE_PROFIL.name)
    _profil_not = ("GOMULU LOGIN profili — giris cerezleri kalici"
                   if LOGIN_PROFIL_KULLAN else "cf_clearance tasinsin diye")
    log.info(f"  DB      : {DB_FILE.name} (WAL)  |  Profil: KALICI "
             f"({_profil_adi}) — {_profil_not}")
    if PROAKTIF_ROTASYON_TUR > 0:
        log.info(f"  ROTASYON: PROAKTIF — her {PROAKTIF_ROTASYON_TUR} turda "
                 f"bir proxy degistirilecek (PX yemeden)")
    else:
        log.info("  ROTASYON: PROAKTIF KAPALI — sadece block/hard block'ta "
                 "proxy degisir")
    log.info(f"  Brave   : version_main={VERSION_MAIN if VERSION_MAIN else 'auto-detect'}")
    log.info("=" * 60)

    if not _tek_instance_al():
        log.error("⛔ BASKA BIR BOT ZATEN CALISIYOR (oto_bot.lock kilitli). "
                  "Iki instance ayni Brave profilini acamaz — bu surec cikiyor. "
                  "Once calisan botu kapat.")
        return

    con = db_init()
    log.info(f"DB hazir: {DB_FILE}")

    gorulmus = goruldu_yukle()
    log.info(f"Yuklenen gorulmus ilan: {len(gorulmus)}")

    driver = None
    tur = 0
    ardarda_driver_hata = 0
    tarama_ardarda_block = 0   # ana tarama block sayaci — kademeli mola icin
    # 21.09.2026 — ISINMA BAYRAGI: proxy DEGISTIGINDE True yapilir; bir
    # sonraki driver kurulumunda _isinma_turu() calisir (ana sayfada insan
    # gibi davranip PX "yeni oturum" puanini yukseltir). Ilk acilista False
    # (orada zaten CF_SONRASI_INCELEME var).
    _isinma_gerek = False
    # 21.09.2026 — PROXY DEGISIMI SADECE ARADA: tarama ORTASINDA (block/
    # px_hard/navigate-asilma) proxy degistirmek yerine bu bayragi kaldiririz;
    # gercek degisim tur SONUNDAKI aralikta yapilir. Boylece yari cozulmus
    # challenge sifirlanmaz ve kullanici elle mudahale edebilir.
    _proxy_degisim_bekliyor = False
    aktif_proxy = _proxy_sec()  # havuz bossa None (proxysiz calisir)
    if PROXY_HAVUZU:
        log.info(f"[PROXY] havuz: {len(PROXY_HAVUZU)} proxy | aktif: {_proxy_kisa(aktif_proxy)}")
    else:
        log.info("[PROXY] havuz bos — proxysiz calisiliyor (.env PROXY_LIST bos)")

    # FAZ 3: detay worker thread (driver_ref mutable container ile paylasilir)
    driver_ref     = {"driver": None}
    ana_sekme_ref  = {"handle": None}   # ana taramanin sekmesi — worker'dan ayri
    con_lock       = threading.Lock()
    stop_event     = threading.Event()
    cf_block_event = threading.Event()   # CF/block sirasinda iki cephe de sussun
    # driver_lock: TEK driver, worker artik driver.get() navigasyonu kullaniyor
    # (fetch() degil) — ana tarama ile sirali erisim sart, yoksa iki thread
    # ayni anda farkli sayfalara navigate etmeye calisip birbirini bozar.
    driver_lock    = threading.Lock()
    worker_th = None
    if DETAY_WORKER_AKTIF:
        worker_th = threading.Thread(
            target=_detay_worker, args=(driver_ref, con_lock, stop_event, cf_block_event, driver_lock),
            daemon=True, name="detay-worker"
        )
        worker_th.start()
    else:
        log.info("[FAZ3] detay worker DEVRE DISI (DETAY_WORKER_AKTIF=False) — "
                 "sadece ana tarama calisacak")

    while True:
        try:
            if not _driver_ayakta_mi(driver):
                if driver is not None:
                    try: driver.quit()
                    except Exception: pass
                log.info(f"Brave baslatiliyor (proxy: {_proxy_kisa(aktif_proxy)}) — CF cikarsa manuel tikla...")
                driver = _driver_olustur(aktif_proxy)
                driver_ref["driver"] = driver   # FAZ 3 worker paylasimi
                ana_sekme_ref["handle"] = driver.current_window_handle
                # Proxy varsa cikis IP dogrula — ev IP'sinden cikiyorsa proxy bozuk
                if aktif_proxy:
                    _ip, _ok = _cikis_ip_dogrula(driver, aktif_proxy)
                    if not _ok:
                        log.error("[PROXY] cikis dogrulamasi BASARISIZ — bu proxy dinlendiriliyor, sonrakine geciliyor")
                        _proxy_durum[aktif_proxy]["dinlen_bitis"] = time.monotonic() + PROXY_DINLENME_SN
                        try: driver.quit()
                        except Exception: pass
                        driver = None
                        yeni_px = _proxy_sec(haric=aktif_proxy)
                        if yeni_px is None:
                            # 21.09.2026 — _proxy_sec artik None dondurur
                            # (hepsi karantinada). Eskiden burada `yeni_px ==
                            # aktif_proxy` kontrolu vardi; None ile bu YANLIS
                            # olur ve aktif_proxy=None yapilip bot proxysiz
                            # kalirdi. Artik kalan karantina suresi kadar
                            # bekleyip tekrar deniyoruz.
                            bekle = _proxy_bekleme_sn(haric=aktif_proxy)
                            log.warning(f"[PROXY] tum proxyler karantinada — "
                                        f"{bekle}sn bekleniyor")
                            _duyarli_bekle(bekle, stop_event)
                            continue
                        aktif_proxy = yeni_px
                        _isinma_gerek = True   # yeni proxy → isinma turu
                        continue
                # 21.09.2026 — KARANTINA SONRASI ISINMA: proxy degisiminden
                # (veya karantinadan) sonra yeni driver kurulduysa, listeye
                # dalmadan ONCE ana sayfada insan gibi davran. PX'in "yeni
                # oturum" puanini yukseltir; soguk giris hard block riskini
                # azaltir. Sadece proxy DEGISTIGINDE calisir (ilk acilista
                # degil — orada zaten CF_SONRASI_INCELEME var).
                if _isinma_gerek:
                    _isinma_turu(driver, stop_event)
                    _isinma_gerek = False
                driver.get(_sayfa_url(0))
                time.sleep(_jitter(RENDER_SETTLE_MIN, RENDER_SETTLE_MAX))
                _cf_sonuc = _cf_gec(driver, 120)
                if _cf_sonuc == "giris":
                    # 21.09.2026 — KULLANICI TALEBI: login duvari gorulunce
                    # DUR. Proxy DEGISTIRME (istek atip durumu kotulestirmez).
                    # Pencereyi one getir, kullanici ELLE giris yapana kadar
                    # SONSUZ bekle (istek YOK). Giris yapilinca devam.
                    log.warning("  🔐 LOGIN DUVARI (acilis) — BOT DURDURULDU. "
                                "Pencerede ELLE giris yapin; giris tamamlaninca "
                                "bot KENDILIGINDEN devam edecek. (Iptal: Ctrl+C)")
                    try:
                        driver.restore_window()
                        driver.maximize_window()
                    except Exception: pass
                    # 21.09.2026 — KRITIK DUZELTME: eskiden _sayfa_duzeldi_mi
                    # ("giris","2fa") kullaniliyordu; login sayfasi
                    # searchResultsItem icermedigi icin 'bos' donuyor, 'bos' da
                    # takili kumesinde olmadigindan ANINDA True donuyordu →
                    # bot duvari "asildi" sanip Tur #1'e geciyordu (log kaniti:
                    # 01:16:53 duvar → 01:17:38 'Giris yapildi' → Tur #1).
                    # Artik _login_duvari_asildi_mi: sayfa BILINEN-KOTU
                    # durumlardan (giris/2fa/block/bos/cf/captcha/px_hard)
                    # cikana kadar bekler. Boylece giris sonrasi ana sayfaya
                    # dusulse bile dogru sekilde "asildi" denir.
                    if _login_duvari_asildi_mi(driver, 24 * 3600, stop_event):
                        log.info("  ✓ Giris yapildi — devam")
                    else:
                        log.warning("  [LOGIN] bekleme durduruldu (kapanis).")
                    continue   # tur baslatmadan basa don (yeni istek YOK)
                if _cf_sonuc:
                    log.info("✓ Sayfa hazir")
                    # 18.09.2026 — CF gecer gecmez tarama baslamiyor artik.
                    # Insan CF'yi cozdukten sonra sayfaya hemen tepki vermez,
                    # birkac saniye/onlarca saniye bakar. Bu bekleme sirasinda
                    # da bir miktar scroll/okuma simulasyonu yapiyoruz.
                    inceleme = _jitter(CF_SONRASI_INCELEME_MIN, CF_SONRASI_INCELEME_MAX)
                    log.info(f"  Insan sayfayi inceliyor... {inceleme:.0f}sn")
                    _insan_gibi_davran(driver)
                    kalan = inceleme - (OKUMA_BEKLEME_MAX + SCROLL_SAYISI_MAX * SCROLL_BEKLEME_MAX)
                    if kalan > 0:
                        time.sleep(kalan)
                    # Pencereyi ekran disina ATMA — CF gelirse gorulmez.
                    # Minimize et: taskbar'da kalir, kullanici istedigi zaman
                    # tikla açar, CF varsa 'I am human' tıklayabilir.
                    try: driver.minimize_window()
                    except Exception: pass
                else:
                    # 22.09.2026 — KULLANICI KARARI: PX/block ekranindayken
                    # proxy/IP DEGISTIRILMEZ. Eski davranis (acilista gecilemedi
                    # → karantina + failover) sahada kisir donguydu: uc proxy de
                    # acilista hard block yiyip ~50sn'de bir Brave'i yeniden
                    # baslatiyordu; veri sifir, IP'ler daha da yaniyordu.
                    # Ayrica dump karsilastirmasi 'px_hard' ile 'captcha'
                    # sayfalarinin AYNI HTML oldugunu gosterdi — yani bu ekran
                    # buyuk ihtimalle elle GECILEBILIR. Artik: pencereyi one al,
                    # HIC istek atma, kullanici elle gecene kadar bekle.
                    log.warning(
                        "  🖐️  PX/CF EKRANI (acilis) — PENCEREDE elle gec. "
                        "Proxy DEGISTIRILMIYOR, yeni istek ATILMIYOR; "
                        "cozuldugun an bot kendiliginden devam eder."
                    )
                    try: driver.restore_window()
                    except Exception: pass
                    if _login_duvari_asildi_mi(driver, 24 * 3600, stop_event):
                        log.info("  ✓ Ekran gecildi — devam")
                        cf_block_event.clear()
                    else:
                        log.warning("  [PX] bekleme durduruldu (kapanis).")
                    continue

            tur += 1
            tur_basla = time.monotonic()
            log.info(f"━━━ Tur #{tur} ━━━")
            yeni = tekrar = fiyat_deg = 0
            tur_toplam = 0
            tur_gorulen_ids = set()  # Is 3 icin bu turda gorulen tum ilan_id'ler
            tur_kesildi = False      # CF/block/bos → o turun kalan sayfalarini tarama

            # Kademeli derinlik: offset=0 her turda, derinler N-turda-bir,
            # sira KARISTIRILMIS (kok neden analizi — bkz. SAYFA_DERINLIK_PLANI)
            tur_offsetleri = _tur_offsetleri(tur)
            log.info(f"  Bu tur offsetleri: {tur_offsetleri}")

            for idx, offset in enumerate(tur_offsetleri):
                url = _sayfa_url(offset)
                log.info(f"  Sayfa {idx+1}/{len(tur_offsetleri)} → pagingOffset={offset}")
                # driver_lock: worker da ayni driver'i (ayri sekmede) kullanabilir —
                # sirali erisim sart. switch_to.window SADECE gercekten birden
                # fazla sekme varsa (DETAY_WORKER_AKTIF=True) cagrilir — Windows'ta
                # bu cagri pencereyi one/aktif getiriyor (18.09.2026 gozlemi:
                # worker kapaliyken TEK sekme var, bu yuzden cagri gereksizdi ve
                # her sayfa taramasinda kullaniciyi rahatsiz ediyordu).
                with driver_lock:
                    try:
                        if len(driver.window_handles) > 1:
                            driver.switch_to.window(ana_sekme_ref["handle"])
                    except Exception:
                        pass  # ana sekme kapanmis olamaz normalde, guvenlik icin yut
                    # 21.09.2026 — NAVIGATE WATCHDOG: proxy asilirsa tara_sayfa
                    # NavigateAsildi firlatir (driver zorla kapatilmistir). Bu
                    # durumda bot DONMAZ; proxy degistirip turu yeniden baslatiriz.
                    try:
                        ilanlar, soup, html, title = tara_sayfa(driver, url)
                    except NavigateAsildi as e:
                        log.error(f"  [NAV] Sayfa asildi ({e}) — proxy degistirilecek")
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        driver = None
                        if aktif_proxy:
                            _proxy_hard_bildir(aktif_proxy)  # asilan proxy'yi dinlendir
                        if PROXY_DEGISIM_SADECE_ARADA:
                            # 21.09.2026 — SADECE ARADA: proxy'yi SIMDI degistirme.
                            # Turu kes; gercek degisim tur sonu araliginda yapilir.
                            log.info("  [NAV] Proxy degisimi TUR SONU araligina ertelendi "
                                     "(PROXY_DEGISIM_SADECE_ARADA=1)")
                            _proxy_degisim_bekliyor = True
                        else:
                            yeni_px = _proxy_sec(haric=aktif_proxy)
                            if yeni_px:
                                aktif_proxy = yeni_px
                                log.info(f"  [NAV] Yeni proxy: {_proxy_kisa(aktif_proxy)}")
                            else:
                                bekle = _proxy_bekleme_sn()
                                log.warning(f"  [NAV] Uygun proxy yok — {bekle}sn bekleniyor")
                                _duyarli_bekle(bekle, stop_event)
                        _isinma_gerek = True
                        tarama_ardarda_block = 0
                        cf_block_event.clear()
                        tur_kesildi = True
                        break   # bu turu kes, disaridaki dongu yeni proxy'yle devam eder
                durum = _sayfa_durumu(html, title)

                if durum != "ok":
                    # 20.09.2026 — TANI: kullanicinin gordugu "basili tut" CF
                    # ekrani 15 Eylul'den beri _sayfa_durumu() tarafindan HIC
                    # 'cf' olarak yakalanmadi (log'da sifir kayit) — yani bu
                    # ekran title bazli tespitten kaciyor, 'block'/'bos' olarak
                    # yanlis siniflaniyor olabilir. Ham title + HTML'in bir
                    # kismini kaydet ki bir sonraki olayda TAHMIN etmek yerine
                    # gercek veriye bakalim.
                    _duvar_dump_yaz(durum, title, html)

                if durum == "ok":
                    tarama_ardarda_block = 0   # basarili — sayaci sifirla
                    if aktif_proxy:
                        _proxy_basari_bildir(aktif_proxy)
                    tur_toplam += len(ilanlar)

                    if offset == 0 and faz0_dump_gerek() and ilanlar:
                        faz0_dump_yap(html, ilanlar, soup)

                    for ilan in ilanlar:
                        try:
                            sonuc = ilan_yaz(con, ilan)
                            gorulmus.add(ilan["ilan_id"])
                            tur_gorulen_ids.add(ilan["ilan_id"])
                            if   sonuc == "yeni":           yeni += 1
                            elif sonuc == "fiyat_degisti":  fiyat_deg += 1
                            else:                            tekrar += 1
                            bucket_guncelle(con, ilan)
                        except Exception as e:
                            log.warning(f"  Ilan yaz hata ({ilan.get('ilan_id')}): {e}")

                    con.commit()
                    if idx < len(tur_offsetleri) - 1:
                        time.sleep(_jitter(SAYFA_ARASI_MIN, SAYFA_ARASI_MAX))
                    continue

                # ─── durum != 'ok' → KRITIK KURAL: kalan sayfalari tarama, cik ───
                tur_kesildi = True
                cf_block_event.set()   # detay worker da sussun

                if durum == "cf":
                    gecti = _cf_bekle(driver, stop_event, ana_sekme_ref)
                    if not gecti:
                        # 19.09.2026 — uzun kor mola KALDIRILDI. Kullanici manuel
                        # mudahale edecegi icin kisa bir bekleme yeterli, dongu
                        # tekrar denedikce CF durumunu yeniden kontrol edecek.
                        log.warning("  [CF] hala gecilemedi — kisa bekleme sonrasi tekrar denenecek")
                        _duyarli_bekle(20, stop_event)
                    tarama_ardarda_block = 0   # CF farkli bir olay, block sayacini bozmasin

                elif durum == "block":
                    tarama_ardarda_block += 1
                    # 22.09.2026 — KULLANICI KARARI: block ekranindayken de
                    # proxy DEGISTIRILMEZ (eskiden esige gelince failover
                    # yapiyordu). Proxy degisimi artik SADECE basarili tarama
                    # sirasinda, tur sonu araliginda yapilan proaktif rotasyonla
                    # olur. Engel ekranlarinda bot bekler, kullanici elle gecer.
                    idx = min(tarama_ardarda_block, len(TARAMA_BLOCK_MOLA_SN)) - 1
                    mola = TARAMA_BLOCK_MOLA_SN[idx]
                    seviye = "KRITIK" if tarama_ardarda_block >= len(TARAMA_BLOCK_MOLA_SN) else ""
                    log.warning(f"  ⛔ {seviye} BLOCK ({tarama_ardarda_block}. kez ust uste, "
                                f"sayfa {len(html or ''):,} byte) → {mola//60} dk mola. "
                                f"Bu surede hic istek atilmiyor.")
                    # Pencereyi goster — bazi 'block' durumlari aslinda gizli bir
                    # challenge/dogrulama olabilir (18.09 gozlemi), kullanici
                    # gorup mudahale edebilsin.
                    try: driver.restore_window()
                    except Exception: pass
                    # Kor uyuma YOK: yeni istek atmadan acik sayfayi izle.
                    # Kullanici challenge'i cozerse hemen devam ederiz.
                    # 21.09.2026 — beklenen=("ok",): 'block' sayfasi da
                    # searchResultsItem icermedigi icin 'bos' donebilir; eski
                    # kod 'bos'u "duzeldi" sayip erken cikiyordu. Artik GERCEK
                    # liste sayfasi ('ok') gorulene kadar bekler.
                    if _sayfa_duzeldi_mi(driver, mola, ("block",), stop_event,
                                         beklenen=("ok",)):
                        log.info("  ✓ Sayfa kendine geldi (challenge cozulmus olabilir) "
                                 "— block sayaci sifirlandi, devam")
                        tarama_ardarda_block = 0

                elif durum == "px_hard":
                    # 22.09.2026 — KULLANICI KARARI: PX ekranindayken proxy/IP
                    # DEGISTIRILMEZ; kullanici elle gecer.
                    #
                    # Eski davranis (kademeli karantina + failover) sahada
                    # kisir donguydu: uc proxy de sirayla hard block yiyip
                    # ~50sn'de bir Brave yeniden baslatiyordu, veri sifirdi ve
                    # her deneme IP'leri biraz daha yakiyordu.
                    #
                    # Ayrica 'px_hard' ile 'captcha' dump'lari karsilastirildi:
                    # HTML'leri AYNI (ayni px-captcha wrapper, ayni captcha.js,
                    # sadece UUID'ler farkli). Yani "cozulemez karaliste" ayrimi
                    # kanitlanmis degil — bu ekran muhtemelen elle gecilebiliyor.
                    log.warning(
                        "  🖐️  PX EKRANI — PENCEREDE elle gec. Proxy "
                        "DEGISTIRILMIYOR, yeni istek ATILMIYOR; cozuldugun an "
                        "bot kendiliginden devam eder."
                    )
                    _duvar_dump_yaz("px_hard", title, html)
                    try: driver.restore_window()
                    except Exception: pass
                    if _login_duvari_asildi_mi(driver, 24 * 3600, stop_event):
                        log.info("  ✓ PX ekrani gecildi — devam")
                        cf_block_event.clear()
                    else:
                        log.warning("  [PX] bekleme durduruldu (kapanis).")
                    tarama_ardarda_block = 0

                elif durum == "captcha":
                    # 20.09.2026 — PerimeterX/HUMAN "Press & Hold" captcha'si.
                    # Eskiden bu 'block' sanilip proxy failover'i (bu proxy
                    # kotu, digerine gec) tetikliyordu — ama bu IP'ye ozgu bir
                    # sert red degil, TARAYICI OTURUMUNA baglı interaktif bir
                    # dogrulama. Proxy degistirmek bunu cozmez, ustune bir de
                    # yari cozulmus challenge'i sifirlayabilir. Bu yuzden ayri
                    # tutuluyor: proxy/block sayacini etkilemiyor.
                    #
                    # 22.09.2026 — OTOMATIK COZUM DEVRE DISI (olculdu, hic ise
                    # yaramadi). Tum log gecmisi: 33 deneme, 0 basari. Her
                    # olayda 3 deneme x ~10sn bosa gidiyordu. Artik dogrudan
                    # kullaniciya birakiliyor. (_px_coz kodu duruyor, cagrilmiyor.)
                    log.warning(
                        "  🖐️  PERIMETERX CAPTCHA — PENCEREDE 'Basılı Tutun' "
                        "dugmesine basip birkac saniye basili tut. Bot yeni "
                        "istek ATMIYOR, proxy DEGISTIRMIYOR; cozuldugun an "
                        "kendiliginden devam eder."
                    )
                    try: driver.restore_window()
                    except Exception: pass
                    # beklenen=("ok",): captcha sayfasi da searchResultsItem
                    # icermez; PX imzalari bir an okunamazsa 'bos' doner ve
                    # eski kod erken "cozuldu" derdi. GERCEK liste bekleniyor.
                    if _login_duvari_asildi_mi(driver, 24 * 3600, stop_event):
                        log.info("  ✓ Captcha cozuldu — devam")
                    else:
                        log.warning("  [PX] bekleme durduruldu (kapanis).")

                elif durum in ("giris", "2fa"):
                    # 19.09.2026 — bot bunu KENDI COZEMEZ; kullanici pencerede
                    # giris yapmali / dogrulamayi tamamlamali.
                    #
                    # 21.09.2026 — KULLANICI TALEBI (KRITIK): "login ekrani
                    # gelince DUR, surekli istek atma. Istek attikca boka
                    # sariyor. Sal, giris yapalim veya kalsin oyle."
                    #
                    # ESKI DAVRANIS (KOTU): kisa manuel pencere → cozulmezse
                    # proxy degistir / 60sn sonra TEKRAR tur baslat → yeni
                    # driver.get() istegi → login duvari yine → KISIR DONGU.
                    # Her istek login duvarini TAZELIYOR ve durumu kotulestiriyor.
                    #
                    # YENI DAVRANIS: login duvari goruldugunde bot TAMAMEN
                    # DURUR. Hicbir istek ATILMAZ (navigasyon yok). Sadece acik
                    # sayfanin title/source'u izlenir. Kullanici ELLE giris
                    # yapinca (veya uzaktan baglanip cozunce) sayfa kendiliginden
                    # degisir ve bot kaldigi yerden devam eder. Kullanici
                    # cozmezse bot SONSUZA KADAR bekler — istedigi gibi.
                    ad = "GIRIS DUVARI" if durum == "giris" else "2 ASAMALI DOGRULAMA"
                    log.warning(
                        f"  🔐 {ad} — sahibinden hesap dogrulamasi istiyor. "
                        f"BOT DURDURULDU: artik HICBIR ISTEK ATILMAYACAK. "
                        f"Pencerede ELLE giris yapin (veya uzaktan baglanip "
                        f"cozun); giris tamamlaninca bot KENDILIGINDEN devam "
                        f"edecek. (Iptal: Ctrl+C)"
                    )
                    # Pencereyi ONE getir — kullanici (veya uzaktan baglanti)
                    # gorebilsin ve mudahale edebilsin.
                    try:
                        driver.restore_window()
                        driver.maximize_window()
                    except Exception: pass
                    # SONSUZ bekleme: kullanici giris yapana kadar istek YOK.
                    # _sayfa_duzeldi_mi navigasyon yapmaz, sadece title/source
                    # okur. Cok uzun tavan (24 saat) → pratikte "suresiz".
                    # 21.09.2026 — KRITIK: _login_duvari_asildi_mi — bkz.
                    # acilis handler'indaki ayni not. Login sayfasi 'bos'
                    # dondugunde yanlislikla "asildi" sanilmasini engeller;
                    # giris sonrasi ana sayfaya dusulse bile dogru calisir.
                    if _login_duvari_asildi_mi(driver, 24 * 3600, stop_event):
                        log.info("  ✓ Duvar asildi (giris yapildi) — devam")
                        tarama_ardarda_block = 0
                    else:
                        # Sadece stop_event (Ctrl+C) ile buraya dusulur.
                        log.warning("  [LOGIN] bekleme durduruldu (kapanis).")
                        tarama_ardarda_block = 0

                else:  # 'bos'
                    log.warning(f"  ⚠️ Sayfa bos icerik (searchResultsItem yok) → "
                                f"1 tur atla, {TARAMA_BOS_MOLA_SN//60} dk mola")
                    _duyarli_bekle(TARAMA_BOS_MOLA_SN, stop_event)

                cf_block_event.clear()
                break   # for sayfa dongusunden cik — bu turun kalanini tarama

            # Is 3 — OLUM TAKIBI RAFA KALDIRILDI.
            # Sayfa dinamigi (trafik saatinde 100+ yeni ilan/dk) sebebiyle 3-tur
            # gorulmeme kurali gercek olumu isaretlemiyor. Detay-sayfa dogrulamasi
            # ile Faz 3-4'te tekrar aciriz. Simdi sadece temiz veri toplama.
            olen_sayi = 0

            goruldu_kaydet(gorulmus)

            gecen = time.monotonic() - tur_basla
            yeni_oran = (yeni / tur_toplam * 100) if tur_toplam else 0
            log.info(f"  DB: yeni={yeni}  tekrar={tekrar}  fiyat_deg={fiyat_deg}  "
                     f"parse={tur_toplam}  yeni_orani=%{yeni_oran:.0f}  "
                     f"toplam_gorulmus={len(gorulmus)}  (tarama {gecen:.0f}sn)")

            if tur_kesildi:
                # CF/block/bos molasi zaten yukarida uygulandi — ekstra tur
                # periyodu molasi eklemeden dogrudan yeni tura gec.
                continue

            # FAZ 3: kuyruga aday ekle (worker arka planda isleyecek — ana dongu beklemez)
            # DETAY_WORKER_AKTIF=False iken atlanir — gereksiz DB sorgusu yapilmaz.
            if DETAY_WORKER_AKTIF:
                p1, p2, p3, ekl = aday_uret(con)
                with _DETAY_LOCK:
                    s = dict(_DETAY_STAT)
                kuyruk_der = _DETAY_KUYRUK.qsize()
                ort_sure = (s["toplam_sure"] / s["basarili"]) if s["basarili"] else 0
                log.info(f"  [FAZ3] kuyruk: eklenen={ekl} (P1={p1})  "
                         f"derinlik={kuyruk_der}/{DETAY_KUYRUK_MAX}  "
                         f"islenen={s['islenen']} (bas={s['basarili']} hata={s['hata']} atlanan={s['atlanan']})  "
                         f"ort={ort_sure:.2f}sn/detay")

            if tur > 2 and yeni_oran >= 85 and tur_toplam >= len(tur_offsetleri) * 15:
                log.warning(
                    f"  ⚠️ KAPASITE DAR: yeni_orani=%{yeni_oran:.0f} → bu turda "
                    f"taranan {len(tur_offsetleri)} sayfanin altinda kalan ilanlar "
                    f"kacabiliyor. SAYFA_DERINLIK_PLANI'na yeni offset ekle "
                    f"(orn. (60, 4)) veya mevcut N degerlerini kucult, botu restart et."
                )

            # ══════════════════════════════════════════════════════════════
            # 21.09.2026 — ERTELENMIS PROXY DEGISIMI (SADECE ARADA kurali).
            # Tarama ORTASINDA (block/px_hard/navigate-asilma) proxy degistirmek
            # yerine _proxy_degisim_bekliyor=True kaldirildi. Gercek degisim
            # BURADA, tur sonu araliginda yapilir. Boylece:
            #   - yari cozulmus challenge sifirlanmaz,
            #   - kullanici elle CF/PX gecebilir,
            #   - proxy degisimi hep "temiz" bir noktada (tur arasi) olur.
            # ══════════════════════════════════════════════════════════════
            if _proxy_degisim_bekliyor:
                _proxy_degisim_bekliyor = False
                if aktif_proxy:
                    yeni_px = _proxy_sec(haric=aktif_proxy)
                    if yeni_px and yeni_px != aktif_proxy:
                        log.info(f"  🔄 ERTELENMIS PROXY DEGISIMI (tur sonu): "
                                 f"{_proxy_kisa(aktif_proxy)} → "
                                 f"{_proxy_kisa(yeni_px)}")
                        try: driver.quit()
                        except Exception: pass
                        driver = None
                        _proxy_profil_temizle(aktif_proxy)
                        aktif_proxy = yeni_px
                        _isinma_gerek = True   # yeni proxy → isinma turu
                        tarama_ardarda_block = 0
                        cf_block_event.clear()
                        _duyarli_bekle(PROAKTIF_ROTASYON_MOLA_SN, stop_event)
                        continue   # yeni proxy'yle driver kurulacak
                    # Uygun yedek yok — karantina suresi kadar bekle, sonra dene
                    bekle = _proxy_bekleme_sn(haric=aktif_proxy)
                    log.warning(f"  [PROXY] ertelenmis degisim icin uygun proxy yok "
                                f"— {bekle}sn bekleniyor")
                    _duyarli_bekle(bekle, stop_event)
                    continue

            # ══════════════════════════════════════════════════════════════
            # 21.09.2026 — PROAKTIF PROXY ROTASYONU (kullanici stratejisi).
            # PX hard block YEMEYI BEKLEMEK yerine, her PROAKTIF_ROTASYON_TUR
            # turda bir proxy'yi KENDILIGINDEN degistir. Boylece her IP daha
            # az tur gorur → PX davranissal skoru yavas yukselir; karantina/
            # hard block dongusune girmeden temiz IP'ler arasinda gezinilir.
            # Yeni proxy'ye gecince _isinma_gerek=True → bir sonraki driver
            # kurulumunda isinma turu calisir (taze oturum).
            # ══════════════════════════════════════════════════════════════
            if (PROAKTIF_ROTASYON_TUR > 0 and aktif_proxy
                    and tur % PROAKTIF_ROTASYON_TUR == 0):
                yeni_px = _proxy_sec(haric=aktif_proxy)
                if yeni_px and yeni_px != aktif_proxy:
                    log.info(f"  🔄 PROAKTIF ROTASYON ({tur}. tur, her "
                             f"{PROAKTIF_ROTASYON_TUR} turda bir): "
                             f"{_proxy_kisa(aktif_proxy)} → "
                             f"{_proxy_kisa(yeni_px)}")
                    try: driver.quit()
                    except Exception: pass
                    driver = None
                    aktif_proxy = yeni_px
                    _isinma_gerek = True   # yeni proxy → isinma turu
                    tarama_ardarda_block = 0
                    cf_block_event.clear()
                    # Kisa "taze oturum" molasi — amac IP degistirmek, uzun
                    # beklemek degil (isinma turu bunun uzerine eklenir).
                    _duyarli_bekle(PROAKTIF_ROTASYON_MOLA_SN, stop_event)
                    continue   # yeni proxy'yle driver kurulacak
                else:
                    log.info(f"  🔄 PROAKTIF ROTASYON zamani ({tur}. tur) ama "
                             f"uygun yedek proxy yok — mevcutla devam")

            if random.random() < UZUN_MOLA_ORAN:
                mola = _jitter(UZUN_MOLA_MIN, UZUN_MOLA_MAX)
                log.info(f"☕ Uzun mola: {mola:.0f}sn ({mola/60:.1f} dk)")
            else:
                hedef = _jitter(TUR_PERIYOT_MIN, TUR_PERIYOT_MAX)
                if random.random() < UZUN_OKUMA_ORAN:
                    ek = _jitter(UZUN_OKUMA_EK_MIN, UZUN_OKUMA_EK_MAX)
                    hedef += ek
                    log.info(f"  📖 Uzun okuma turu: +{ek:.0f}sn")
                mola = max(TUR_TABAN_BEKLEME, hedef - gecen)
                log.info(f"  Mola: {mola:.0f}sn (hedef periyot {hedef:.0f}sn)")

            # ══════════════════════════════════════════════════════════════
            # FAZ 2.5 — KOHORT TAKIBI, MOLANIN ICINDE.
            # 24.09.2026: takip once mola HESABINDAN ONCE cagriliyordu; ama
            # `gecen` yukarida (satir ~4675) olculdugu icin takibin suresi
            # molaya sayilmiyor, tur dongusunun USTUNE ekleniyordu —
            # her 3. turda ~50-110sn, ortalama %7-14 yavaslama. Ilan kacmasi
            # demek. Artik mola SURESINDEN dusuluyor: bot zaten bekliyorken
            # calisiyor, tur periyodu degismiyor.
            #
            # Buraya sadece TEMIZ turda gelinir (tur_kesildi olsaydi yukarida
            # `continue` edilmisti) — block/CF turunda takip hic calismaz.
            # ══════════════════════════════════════════════════════════════
            if TAKIP_AKTIF:
                _t0 = time.monotonic()
                if tur % TAKIP_KOHORT_TUR == 0:
                    _takip_kohort_ekle(con)
                if tur % TAKIP_TUR_ARALIGI == 0:
                    _takip_kontrol(driver, con, driver_lock, stop_event)
                _harcanan = time.monotonic() - _t0
                if _harcanan > 1.0:
                    _yeni_mola = max(TAKIP_MOLA_TABANI, mola - _harcanan)
                    log.info(f"  [TAKIP] {_harcanan:.0f}sn moladan dusuldu "
                             f"→ kalan mola {_yeni_mola:.0f}sn "
                             f"(tur periyodu DEGISMEDI)")
                    mola = _yeni_mola
            time.sleep(mola)

        except KeyboardInterrupt:
            log.info("Kapatildi (Ctrl+C).")
            break
        except WebDriverException as e:
            # 20.09.2026 — chromedriver istisnalari ~20 satirlik stacktrace
            # tasiyor; her turda loglaninca oto.log okunamaz hale geliyordu
            # (teshis yavasliyor). Sadece ilk satir yeterli.
            ozet = (str(e).strip().splitlines() or ["?"])[0][:180]
            ardarda_driver_hata += 1
            log.error(f"Driver hata ({ardarda_driver_hata}. kez): {ozet}")
            if not _driver_ayakta_mi(driver):
                log.warning(f"Driver olmus → {OLU_SURUCU_BEKLEME}sn sonra yeniden kurulacak")
                try: driver.quit()
                except Exception: pass
                driver = None
                ardarda_driver_hata = 0
                time.sleep(OLU_SURUCU_BEKLEME)
            elif ardarda_driver_hata >= DRIVER_HATA_ESIK:
                # Driver "ayakta" gorunuyor ama is yapamiyor — zorla yenile.
                # Bu olmadan bot teknik olarak sag ama islevsiz bir driver'la
                # sonsuz donguye girebiliyor.
                log.warning(f"{ardarda_driver_hata} ust uste driver hatasi → "
                            f"driver ZORLA yeniden kuruluyor")
                try: driver.quit()
                except Exception: pass
                driver = None
                ardarda_driver_hata = 0
                time.sleep(GECICI_HATA_BEKLEME)
            else:
                time.sleep(GECICI_HATA_BEKLEME)
        except Exception as e:
            # 22.09.2026 — CHROMEDRIVER BAGLANTI TIMEOUT'U (en buyuk verim
            # kaybi, olculdu). urllib3'un ReadTimeoutError'u WebDriverException
            # DEGIL, bu yuzden yukaridaki driver-kurtarma dalina hic girmiyordu:
            # bot ayni asili driver'la tekrar deniyor, her deneme 120sn timeout
            # + 15sn bekleme yakiyordu. Olcum (21-22.09): tek olaydan sonra
            # ~8 dk bos gecen sure, turlarin %64'u verisiz.
            mesaj = str(e)
            if "Read timed out" in mesaj or "HTTPConnectionPool" in mesaj:
                # 23.09.2026 — NEREDE asildigini KAYDET. Log'dan regex ile
                # cikarim yapmak sinira dayandi; asilmanin hangi cagrida
                # oldugunu bilmeden kok neden bulunamiyor. Tam stacktrace
                # log'u kirletiyordu (20+ satir), bu yuzden sadece
                # oto_bot.py'ye ait SON kareyi yaziyoruz — tek satir, kesin bilgi.
                import traceback
                nerede = "?"
                try:
                    kareler = [k for k in traceback.extract_tb(e.__traceback__)
                               if "oto_bot.py" in k.filename]
                    if kareler:
                        k = kareler[-1]
                        nerede = f"{k.name}() satir {k.lineno}: {(k.line or '').strip()[:70]}"
                except Exception:
                    pass
                log.error(f"Chromedriver cevap vermiyor (baglanti timeout) → "
                          f"driver yeniden kuruluyor  [ASILDIGI YER: {nerede}]")
                _arka_planda_kapat(driver)
                driver = None
                ardarda_driver_hata = 0
                time.sleep(GECICI_HATA_BEKLEME)
            else:
                log.error(f"Beklenmeyen hata: {e}", exc_info=True)
                time.sleep(GECICI_HATA_BEKLEME)

    # FAZ 3 worker durdur (aktifse)
    stop_event.set()
    if worker_th is not None:
        try: _DETAY_KUYRUK.put_nowait(None)
        except Exception: pass
        try: worker_th.join(timeout=5)
        except Exception: pass

    try: con.commit(); con.close()
    except Exception: pass
    try: driver.quit()
    except Exception: pass


if __name__ == "__main__":
    main()
