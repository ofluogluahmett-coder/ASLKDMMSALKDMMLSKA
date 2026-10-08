// ════════════════════════════════════════════════════════════════════
//  OTO KELEPIR — SAYFA ICI TOPLAYICI (content script)
//
//  NEDEN BOYLE: olculen tum alternatifler elendi — IP temiz (ayni IP'den
//  elle gezinme serbest), cerezler tam, parmak izi temiz, tempo fark
//  etmedi. Geriye TEK fark kaldi: tarayicinin DISARIDAN SURULMESI
//  (WebDriver + CDP + --remote-debugging-port + gecici profil).
//
//  Bu dosya kullanicinin KENDI tarayicisinda, KENDI profilinde, acik
//  duran sahibinden sekmesinde calisir. Istekler sayfanin kendi JS
//  baglamindan, sayfanin KENDI XMLHttpRequest yolundan gider.
//  Hicbir dogrulama basligi elle uretilmez; sitenin kendi SDK'si kendi
//  isini yapar (kullanici tikladiginda oldugu gibi).
//
//  Ne yapar: acik liste sayfasini dar bantta rastgele araliklarla
//  tazeler, kartlari cikarir, DAHA ONCE GORMEDIGI ID'leri yerel sunucuya
//  yollar. Sayfayi degistirmez, tiklama simule etmez.
// ════════════════════════════════════════════════════════════════════

(() => {
  "use strict";

  const ARALIK_MIN = 45000;      // 45 sn  — sekme ONDE
  const ARALIK_MAX = 75000;      // 75 sn  (birebir periyot makine imzasi)
  // 07.10.2026 — sekme ARKADA iken de taranir, sadece daha seyrek.
  // OLCUM: eskiden gizli sekmede HIC tur atmiyordu; 3 dakikada tek tur
  // dustu (kullanici baska pencereye gectigi an sistem duruyordu). Gun
  // boyu gozetimsiz kosuda sekme kesin arkada kalir, yani o kural
  // toplayiciyi tamamen susturuyordu. Arkada acik duran bir sekmenin
  // periyodik istek atmasi zaten normal (tarayici da timer'lari dakikada
  // bire kisiyor, biz onun ustunde kaliyoruz).
  // 08.10.2026 — ARKADA da ONDEKI tempoya cekildi. Kullanicinin birinci
  // onceligi "yeni ilan ANINDA dusmeli"; sekme neredeyse her zaman
  // arkada oldugu icin 90-150 sn'lik seyrek tempo pratikte TEK gecerli
  // tempoydu ve her ilan ortalama ~60 sn gecikmeyle dusuyordu.
  // 56-80 sn araligi daha once PX'siz olculmustu, o bandin icinde
  // kaliyoruz.
  const GIZLI_MIN = 50000;       // 50 sn  — sekme ARKADA
  const GIZLI_MAX = 80000;       // 80 sn

  const SUNUCU = "http://127.0.0.1:8765/ilan";
  const AYAR_UC = "http://127.0.0.1:8765/ayar";

  // Hangi kategorideyiz? Sunucu yonlendirmeyi buna gore yapiyor.
  const KATEGORI =
    location.pathname.indexOf("masaustu-donanim") >= 0 ? "pc" :
    location.pathname.indexOf("oyun-konsolu") >= 0 ? "konsol" :
    "otomobil";

  // ── CANLI AYAR (08.10.2026) ────────────────────────────────────────
  // Her turda yerel sunucudan okunur. Sebep: bir gun icinde tempo,
  // deney varyanti ve filtre degisiklikleri icin kullaniciya ALTI KEZ
  // "uzantiyi yenile + F5" dedim. Ayar sunucudan gelince deneyler
  // tarayiciya hic dokunulmadan yapilabiliyor.
  // Sunucu kapaliysa veya cevap bozuksa asagidaki varsayilanlar gecerli.
  let ayar = {
    durdur: false, aralik_min_sn: 50, aralik_max_sn: 80,
    varyant_mod: "ab", ofsetler: [0], sayfa_boyu: 50
  };

  // ── ZAMAN ASIMLI FETCH (08.10.2026 — KILITLENME DUZELTMESI) ───────
  // OLCUM: toplayici 14:59'da sessizce durdu. Sekme acikti, sayfa
  // saglamdi (challenge yok), bekci sunucudan 427 sn sessizlik
  // olcuyordu. Sebep: fetch cagrilarina zaman asimi koymamistim. Bir
  // istek ASILI kalirsa await hic donmez, finally CALISMAZ ve
  // "calisiyor" bayragi sonsuza kadar true kalir -> sonraki her tur
  // aninda geri doner -> toplayici KALICI olarak durur.
  // XHR'in 20 sn'lik timeout'u vardi ama fetch'ler atlanmisti.
  async function fetchZamanli(url, secenek, msn) {
    const kesici = new AbortController();
    const zamanlayici = setTimeout(() => kesici.abort(), msn);
    try {
      return await fetch(url, { ...(secenek || {}), signal: kesici.signal });
    } finally {
      clearTimeout(zamanlayici);
    }
  }

  async function ayariTazele() {
    try {
      const r = await fetchZamanli(AYAR_UC, { cache: "no-store" }, 5000);
      if (!r.ok) return;
      const y = await r.json();
      if (y && typeof y === "object") {
        for (const k of Object.keys(ayar)) {
          if (y[k] !== undefined && y[k] !== null) ayar[k] = y[k];
        }
      }
    } catch (e) { /* sunucu kapali — varsayilanlarla devam */ }
  }
  // Iki tur arasinda en az bu kadar beklenir. Hem sayfa zamanlayicisi
  // hem servis calisaninin alarmi tur tetikledigi icin cift tura karsi
  // emniyet; ayrica PX'siz olculmus 56-80 sn bandinin altina inilmez.
  const EN_AZ_ARA = 40000;

  const gorulen = new Set();
  let calisiyor = false;
  let sonTurAn = 0;
  // OLCUM (08.10): sayfa tarafinda 13 tur dondu ama hicbiri sunucuya
  // ulasmadi — content script -> service worker mesaji 20 dakika boyunca
  // koptu (bekci kendi POST'larini atabiliyordu, yani sunucu ve ag
  // ayaktaydi). Tek yola bagli kalmamak icin artik ONCE dogrudan POST
  // denenir, tutmazsa worker'a duser. Basarisizliklar sayilip bir
  // sonraki ozetle bildirilir, boylece bir daha korlesmeyiz.
  let hataUstUste = 0;
  let sonHata = "";
  // Tur sayaci sekme yenilenince sifirlanmasin (olcumu bozuyor);
  // sessionStorage ayni sekmede reload'u atlatir.
  let sayac = 0;
  try {
    sayac = parseInt(sessionStorage.getItem("ok_tur") || "0", 10) || 0;
  } catch (e) { /* ozel pencere / kisitli depolama */ }

  const log = (...a) => console.log("[oto-kelepir]", ...a);

  function kartlariCikar(html) {
    const kap = document.createElement("div");
    kap.innerHTML = html;
    const out = [];
    kap.querySelectorAll(".searchResultsItem").forEach((k) => {
      const id = k.getAttribute("data-id");
      if (!id || !/^\d+$/.test(id)) return;   // nativeAd kartlari elenir
      const al = (s) => {
        const e = k.querySelector(s);
        return e ? e.textContent.trim() : "";
      };
      const coklu = (s) => {
        const r = [];
        k.querySelectorAll(s).forEach((a) => {
          const v = a.textContent.trim();
          if (v) r.push(v);
        });
        return r;
      };
      const attr = coklu(".searchResultsAttributeValue");   // yil, km
      const tag = coklu("td.searchResultsTagAttributeValue"); // marka, seri, model
      // il/ilce <br> ile ayrilmis; textContent'te satir sonu OLMUYOR
      // (olcum: il+ilce "TekirdagCerkezkoy" diye birlesik yaziliyordu).
      const locEl = k.querySelector("td.searchResultsLocationValue");
      const loc = locEl
        ? locEl.innerHTML.split(/<br\s*\/?>/i)
            .map((x) => x.replace(/<[^>]*>/g, "").trim()).filter(Boolean)
        : [];
      const a = k.querySelector(".searchResultsTitleValue a, a.classifiedTitle");
      out.push({
        id: id,
        baslik: al(".searchResultsTitleValue"),
        fiyat: al(".searchResultsPriceValue"),
        yil: attr[0] || "",
        km: attr[1] || "",
        marka: tag[0] || "",
        seri: tag[1] || "",
        model: tag[2] || "",
        il: loc[0] || "",
        ilce: loc[1] || "",
        magaza: !!k.querySelector("a.store-icon"),
        url: a ? a.href : "https://www.sahibinden.com/ilan/" + id + "/detay"
      });
    });
    return out;
  }

  // ── ONBELLEK KIRMA DENEYI (08.10.2026) ────────────────────────────
  // OLCUM: sahibinden bize DONDURULMUS bir liste veriyor ve bu goruntu
  // ~5-6 dakikada bir toptan tazeleniyor (icerik parmak izi ust uste
  // ayni kaliyor, sonra 50 kartin 50'si birden degisiyor). Olculen
  // sicrama araliklari: 5:00, 5:35, 5:54, 5:39. Oysa kategoriye dakikada
  // ~5 yeni ilan giriyor. Yani ne kadar sik istek atarsak atalim ayni
  // kopyayi okuyoruz; sorun tempo DEGIL, onbellek.
  // Sayfa yenilemesi de (kullanicinin F5'i) aninda tazelemedi, yani
  // navigasyon da kirmiyor.
  // DENEY: turlar donusumlu olarak (a) duz adres, (b) adres + "_=<ms>"
  // ile cekilir. "_" parametresi jQuery'nin kendi cache:false yolu ve
  // sahibinden jQuery kullaniyor, yani sitenin kendi istekleriyle ayni
  // bicim — uydurma bir baslik veya token DEGIL.
  // Hangi varyantin taze icerik getirdigini sunucu parmak izinden
  // gorecek; kazanan varyanta gecilecek.
  // Ofset rotasyonu: sitenin KENDI sayfalama adresi kullanilir
  //   /otomobil?pagingOffset=50&pagingSize=50&sorting=date_desc
  // (bu bicim sayfanin kendi linklerinden alindi, uydurma DEGIL).
  // OLCUM: 50 slotluk birinci sayfa ~1 dakikada tamamen devriliyor;
  // ikinci sayfayi da izlemek kayip riskini dusurur ve onbellek adres
  // basina tutuluyorsa pencereler kayik oldugu icin gecikmeyi de dusurur.
  function hedefAdres(tur) {
    const ofsetler = (ayar.ofsetler && ayar.ofsetler.length)
      ? ayar.ofsetler : [0];
    const ofs = ofsetler[tur % ofsetler.length];
    let url;
    if (ofs === 0) {
      url = location.pathname + "?sorting=date_desc&pagingSize=" +
            (ayar.sayfa_boyu || 50);
    } else {
      url = location.pathname + "?pagingOffset=" + ofs +
            "&pagingSize=" + (ayar.sayfa_boyu || 50) +
            "&sorting=date_desc";
    }
    let varyant = "duz";
    const mod = ayar.varyant_mod || "duz";
    if (mod === "cachebust" || (mod === "ab" && tur % 2 === 1)) {
      url += "&_=" + Date.now();
      varyant = "cachebust";
    }
    if (ofs !== 0) varyant += "+ofs" + ofs;
    return { adres: url, varyant: varyant };
  }

  function listeyiCek(url) {
    // Sayfanin NORMAL XMLHttpRequest yolu. Sitenin kendi sayfalama
    // istegiyle ayni bicim; PX SDK'si bu yolu sardigi icin dogrulamayi
    // kendi ekler — biz hicbir basligi elle uretmiyoruz.
    return new Promise((cz) => {
      const x = new XMLHttpRequest();
      x.open("GET", url, true);
      x.withCredentials = true;
      x.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      x.timeout = 20000;
      x.onload = () => cz({ durum: x.status, metin: x.responseText || "" });
      x.onerror = () => cz({ durum: 0, metin: "" });
      x.ontimeout = () => cz({ durum: 0, metin: "" });
      x.send();
    });
  }

  async function yolla(paket) {
    // 1. YOL — dogrudan yerel sunucuya. Sunucu CORS + Private Network
    //    Access basliklarini veriyor, bu yuzden https sayfasindan
    //    127.0.0.1'e POST edilebiliyor. Service worker aradan cikar.
    try {
      const r = await fetchZamanli(SUNUCU, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kaynak: "uzanti-dogrudan", kategori: KATEGORI,
          kartlar: paket.kartlar, html: paket.html, ozet: paket.ozet
        })
      }, 20000);
      if (r.ok) {
        return { ok: true, yol: "dogrudan",
                 cevap: (await r.text()).slice(0, 120) };
      }
    } catch (e) { /* 2. yola gec */ }

    // 2. YOL — service worker uzerinden (eski yol, yedek).
    try {
      const cevap = await chrome.runtime.sendMessage(paket);
      return { ok: true, yol: "worker",
               cevap: (cevap && cevap.sonuc) || "" };
    } catch (e) {
      return { ok: false, yol: "yok", cevap: String(e).slice(0, 120) };
    }
  }

  async function birTur(kaynak) {
    if (calisiyor) return;
    if (Date.now() - sonTurAn < EN_AZ_ARA) return;   // cift tur emniyeti
    calisiyor = true;
    sonTurAn = Date.now();
    sayac++;
    try { sessionStorage.setItem("ok_tur", String(sayac)); } catch (e) {}
    const gizli = document.visibilityState !== "visible";
    try {
      await ayariTazele();
      if (ayar.durdur) {
        log("tur", sayac, "— ayar.durdur=true, atlandi");
        return;
      }
      const dny = hedefAdres(sayac);
      const { durum, metin } = await listeyiCek(dny.adres);
      if (durum !== 200 || !metin) {
        log("tur", sayac, "durum", durum, "— atlandi");
        return;
      }
      // ── SITENIN KENDI ARA SAYFASI (08.10.2026) ───────────────────
      // OLCUM: 15:10-15:11'de iki tur "sayfada=0" dondu ve PX sanildi.
      // Kaydedilen ham sayfa 7,5 KB ve basligi "sahibinden.com
      // Yukleniyor" — yani sitenin KENDI /cs/tloading ara sayfasi.
      // Bu ne PX ne Cloudflare; gecici ve kendiliginden gecen bir
      // durum (bir sonraki tur 51 ilanla dondu). Ucunu birbirinden
      // ayirmazsak her birine yanlis tepki veririz.
      if (/cs\/tloading|btn-continue/i.test(metin.slice(0, 6000)) ||
          /<title[^>]*>[^<]*Y[uü]kleniyor/i.test(metin.slice(0, 3000))) {
        log("tur", sayac, "ARA SAYFA (sahibinden yukleniyor) — gecici");
        try {
          await yolla({
            tip: "ilanlar", kartlar: [], html: null,
            ozet: { tur: sayac, sayfada: 0, yeni: 0, gizli: gizli,
                    ara_sayfa: true, kategori: KATEGORI,
                    tetik: kaynak || "?",
                    url: location.pathname + location.search }
          });
        } catch (e) { /* sunucu kapali olabilir */ }
        return;
      }
      if (/denied|px-captcha/i.test(metin.slice(0, 4000))) {
        // 08.10.2026: eskiden burada sessizce donuluyordu ve sunucu
        // "veri yok" goruyordu — challenge ile "sekme olmus" ayirt
        // edilemiyordu. Artik challenge de BILDIRILIYOR; panel bunu
        // gosterir, bekci de bosuna yenileme yapmaz.
        log("tur", sayac, "CHALLENGE — bildirilip beklenecek");
        try {
          await yolla({
            tip: "ilanlar", kartlar: [], html: null,
            ozet: { tur: sayac, sayfada: 0, yeni: 0, gizli: gizli,
                    challenge: true, tetik: kaynak || "?",
                    url: location.pathname + location.search }
          });
        } catch (e) { /* sunucu kapali olabilir */ }
        return;
      }
      const kartlar = kartlariCikar(metin);
      const yeni = kartlar.filter((k) => !gorulen.has(k.id));
      kartlar.forEach((k) => gorulen.add(k.id));
      log("tur", sayac, gizli ? "(arkada)" : "(onde)",
          "[" + (kaynak || "?") + "]",
          "| sayfada", kartlar.length, "| yeni", yeni.length);
      // 07.10.2026 — NABIZ: eskiden SADECE yeni ilan varken POST atiyordu;
      // yeni ilan yoksa sunucu sessiz kaliyor ve "calisiyor ama yeni ilan
      // yok" ile "durmus" ayirt edilemiyordu. Gun boyu gozetimsiz kosu icin
      // bu korluk kabul edilemez. Artik HER tur ozet gider (yeni liste bos
      // olsa bile); sunucu sadece yeni olanlari DB'ye yazar.
      const paket = {
          tip: "ilanlar",
          kartlar: yeni,
          // 07.10.2026 — HAM HTML: sunucu bunu oto_kopru'ya verip otobotun
          // KANITLANMIS ayristiricisindan geciriyor (marka/seri/model, motor
          // hacmi/tipi/paket, il/ilce, cop tespiti) ve zengin semaya
          // (oto_hafiza.db) yaziyor; kelepir skorlamasi oradan besleniyor.
          // Buradaki JS ayristirmasi sadece ID izi + hafif tablo icin kalir.
          // Yalnizca YEREL sunucuya gider (127.0.0.1), disariya CIKMAZ.
          html: metin,
          ozet: { tur: sayac, sayfada: kartlar.length, yeni: yeni.length,
                  gizli: gizli, tetik: kaynak || "?",
                  varyant: dny.varyant,
                  // 08.10.2026 — KATEGORI: ayni uzanti hem otomobil hem
                  // PC bileseni listesini izliyor. Sunucu buna gore
                  // yonlendiriyor: otomobil -> oto DB + kelepir kopru,
                  // pc -> apex_predator'un KelepirMotor'u. Her kategori
                  // KENDI sekmesinde kendi dongusuyle dondugu icin
                  // mevcut tempo (50-80 sn) hicbir kategoride dusmez.
                  kategori: KATEGORI,
                  url: location.pathname + location.search,
                  // 08.10.2026 TESHIS: bazi turlarda 51 kartin 51'i de
                  // "yeni" cikti, oysa olculen hiz dakikada ~3.7. Ya
                  // sekmedeki ADRES degisiyor ya da site bize donusumlu
                  // olarak iki farkli liste anlik goruntusu veriyor.
                  // Sayfanin ilk/son ID'si bu ikisini ayirt eder.
                  ilk_id: (kartlar[0] || {}).id || "",
                  son_id: (kartlar[kartlar.length - 1] || {}).id || "",
                  // Onceki turlarda kac gonderim basarisiz oldu?
                  kayip: hataUstUste, son_hata: sonHata || null }
      };
      const sonuc = await yolla(paket);
      if (sonuc.ok) {
        if (hataUstUste) {
          log("  (onceki", hataUstUste, "gonderim basarisizdi, duzeldi)");
        }
        hataUstUste = 0;
        sonHata = "";
        log("  sunucu [" + sonuc.yol + "]:", sonuc.cevap);
      } else {
        hataUstUste++;
        sonHata = sonuc.cevap;
        log("  GONDERILEMEDI (ust uste", hataUstUste + "):", sonuc.cevap);
      }
    } catch (e) {
      log("tur", sayac, "hata:", String(e).slice(0, 140));
    } finally {
      calisiyor = false;
    }
  }

  function zamanla() {
    // SIKISMA KURTARMA: "calisiyor" bayragi takili kalirsa toplayici
    // KALICI olarak susar — 08.10.2026'da tam bu oldu (14:59'da durdu,
    // sekme ve sayfa saglamdi). Zaman asimlari artik bunu onluyor ama
    // ikinci bir emniyet olarak bir tur 3 dakikayi gecerse bayrak
    // ZORLA birakilir. Sessiz kalici durus kabul edilemez.
    if (calisiyor && Date.now() - sonTurAn > 180000) {
      log("UYARI: tur", sayac, "sikismis — bayrak zorla birakildi");
      calisiyor = false;
    }
    // Sekme arkadaysa daha seyrek, ama DURMAZ (gun boyu kosu icin sart).
    // Bant artik sunucudan geliyor (ayar.json); sunucu kapaliysa
    // asagidaki sabitler yedek.
    const gizli = document.visibilityState !== "visible";
    let alt = (ayar.aralik_min_sn || 0) * 1000;
    let ust = (ayar.aralik_max_sn || 0) * 1000;
    if (!alt || !ust || ust < alt) {
      alt = gizli ? GIZLI_MIN : ARALIK_MIN;
      ust = gizli ? GIZLI_MAX : ARALIK_MAX;
    }
    const bekle = alt + Math.random() * (ust - alt);
    setTimeout(async () => {
      await birTur("sayfa");
      zamanla();
    }, bekle);
  }

  // ── SERVIS CALISANI TETIKLEMESI (08.10.2026) ──────────────────────
  // OLCUM: arkadaki sekmede 50-80 sn'lik zamanlayici yazilmasina ragmen
  // turlar 118 sn arayla dustu. Sebep ayar degil, TARAYICI: arka plan
  // sekmelerinde zamanlayicilari dakikada bire kisiyor ve 5 dakikadan
  // sonra dakika sinirlarina hizaliyor. Yani sayfa icindeki zamanlayici
  // ile arka planda 60-120 sn'nin altina INILEMEZ.
  // Cozum: tempoyu servis calisaninin chrome.alarms'ina tasidik — o bu
  // kisitlamaya tabi DEGIL. Alarm dakikada bir "tur" diyor, sayfa da
  // 0-15 sn rastgele gecikme ekliyor (birebir periyot makine imzasi).
  // Sayfa zamanlayicisi YEDEK olarak duruyor: servis calisani olurse
  // toplama yine devam eder. EN_AZ_ARA cift turu engelliyor.
  try {
    chrome.runtime.onMessage.addListener((mesaj) => {
      if (mesaj && mesaj.tip === "tur_istegi") {
        setTimeout(() => birTur("alarm"), Math.random() * 15000);
      }
      return false;
    });
  } catch (e) { /* baglam gecersiz (uzanti yeniden yuklenmis) */ }

  log("yuklendi —", location.pathname + location.search);
  birTur("acilis");
  zamanla();
})();
