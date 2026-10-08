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
  const ARALIK_MAX = 90000;      // 90 sn  (birebir periyot makine imzasi)
  // 07.10.2026 — sekme ARKADA iken de taranir, sadece daha seyrek.
  // OLCUM: eskiden gizli sekmede HIC tur atmiyordu; 3 dakikada tek tur
  // dustu (kullanici baska pencereye gectigi an sistem duruyordu). Gun
  // boyu gozetimsiz kosuda sekme kesin arkada kalir, yani o kural
  // toplayiciyi tamamen susturuyordu. Arkada acik duran bir sekmenin
  // periyodik istek atmasi zaten normal (tarayici da timer'lari dakikada
  // bire kisiyor, biz onun ustunde kaliyoruz).
  const GIZLI_MIN = 90000;       // 90 sn  — sekme ARKADA
  const GIZLI_MAX = 150000;      // 150 sn

  const gorulen = new Set();
  let calisiyor = false;
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

  async function birTur() {
    if (calisiyor) return;
    calisiyor = true;
    sayac++;
    try { sessionStorage.setItem("ok_tur", String(sayac)); } catch (e) {}
    const gizli = document.visibilityState !== "visible";
    try {
      const { durum, metin } = await listeyiCek(location.href);
      if (durum !== 200 || !metin) {
        log("tur", sayac, "durum", durum, "— atlandi");
        return;
      }
      if (/denied|px-captcha/i.test(metin.slice(0, 4000))) {
        log("tur", sayac, "challenge isareti — atlandi, bir sonrakini bekle");
        return;
      }
      const kartlar = kartlariCikar(metin);
      const yeni = kartlar.filter((k) => !gorulen.has(k.id));
      kartlar.forEach((k) => gorulen.add(k.id));
      log("tur", sayac, gizli ? "(arkada)" : "(onde)",
          "| sayfada", kartlar.length, "| yeni", yeni.length);
      // 07.10.2026 — NABIZ: eskiden SADECE yeni ilan varken POST atiyordu;
      // yeni ilan yoksa sunucu sessiz kaliyor ve "calisiyor ama yeni ilan
      // yok" ile "durmus" ayirt edilemiyordu. Gun boyu gozetimsiz kosu icin
      // bu korluk kabul edilemez. Artik HER tur ozet gider (yeni liste bos
      // olsa bile); sunucu sadece yeni olanlari DB'ye yazar.
      try {
        const cevap = await chrome.runtime.sendMessage({
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
                  gizli: gizli, url: location.pathname + location.search }
        });
        log("  sunucu:", cevap && cevap.sonuc);
      } catch (e) {
        log("  sunucuya yollanamadi:", String(e).slice(0, 120));
      }
    } catch (e) {
      log("tur", sayac, "hata:", String(e).slice(0, 140));
    } finally {
      calisiyor = false;
    }
  }

  function zamanla() {
    // Sekme arkadaysa daha seyrek, ama DURMAZ (gun boyu kosu icin sart).
    const gizli = document.visibilityState !== "visible";
    const alt = gizli ? GIZLI_MIN : ARALIK_MIN;
    const ust = gizli ? GIZLI_MAX : ARALIK_MAX;
    const bekle = alt + Math.random() * (ust - alt);
    setTimeout(async () => {
      await birTur();
      zamanla();
    }, bekle);
  }

  log("yuklendi —", location.pathname + location.search);
  birTur();
  zamanla();
})();
