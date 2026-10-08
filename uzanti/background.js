// ════════════════════════════════════════════════════════════════════
//  OTO KELEPIR — ARKA PLAN ISCISI (service worker)
//
//  IKI GOREVI VAR:
//
//  1) POSTACI — content script'ten gelen ilanlari yerel sunucuya
//     (127.0.0.1) yollar. Bu isi content script'ten DOGRUDAN yapmak
//     CORS'a takiliyor; service worker host_permissions ile yollayabilir.
//
//  2) BEKCI — dakikada bir kontrol eder: sahibinden liste sekmesi
//     duruyor mu, ondan veri geliyor mu?
//
//     OLCUM (08.10.2026): sekme arka plana dustukten ~5 dakika sonra
//     turlar TAMAMEN durdu (son tur 10:08:25, sonraki 10 dakika sessiz).
//     Sekme ne kapanmisti ne de discard edilmisti (`discarded=false`) —
//     tarayici onu DONDURMUSTU (tab freezing). Donmus sekmede sayfanin
//     zamanlayicilari hic calismaz; sekme acik gorunur ama toplayici
//     olur. Bekci bu durumda sekmeyi BIR KEZ yeniler ve panele rapor
//     verir, boylece kullanici evde olmasa da sistem ayakta kalir.
//
//     Asil cozum kullanici tarafinda: sekme KENDI penceresinde ve o
//     pencerenin AKTIF sekmesi olarak kalirsa hic donmaz. Bekci yedek.
//
//  sahibinden'e veri istegi ATMAZ; tek istedigi yerel sunucu + gerekirse
//  var olan sekmenin yenilenmesi.
// ════════════════════════════════════════════════════════════════════

const SUNUCU = "http://127.0.0.1:8765/ilan";
const NABIZ = "http://127.0.0.1:8765/nabiz";
const DURUM = "http://127.0.0.1:8765/durum";
const LISTE_DESEN = "https://www.sahibinden.com/otomobil*";

const SESSIZ_ESIK = 240;     // sn — bu kadar veri yoksa sekme olmus say
const YENILEME_ARASI = 300;  // sn — iki yenileme arasi en az bu kadar

// ── 1) POSTACI ──────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((mesaj, _gonderen, cevapla) => {
  if (!mesaj || mesaj.tip !== "ilanlar") return false;
  // NABIZ: kartlar bos olsa bile yollanir, boylece sunucu "calisiyor ama
  // yeni ilan yok" halini gorur ("durmus" ile karismasin).
  const kartlar = mesaj.kartlar || [];
  (async () => {
    // OLCUM (08.10): bu yazim once 'await'siz birakilmisti; service
    // worker fetch biter bitmez uyudugu icin kayit diske DUSMUYORDU ve
    // bekci sessiz_sn'i hep null goruyordu — 10 dakika veri gelmemesine
    // ragmen "izliyor" dedi, sekmeyi yenilemedi. Artik bekleniyor.
    try {
      await chrome.storage.local.set({
        sonVeri: Math.floor(Date.now() / 1000)
      });
    } catch (e) { /* depolama kisitli */ }
    try {
      const r = await fetch(SUNUCU, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kaynak: "uzanti", kartlar: kartlar, ozet: mesaj.ozet || null,
          // Ham liste HTML'i — sunucu zengin semaya yazmak icin otobotun
          // ayristiricisina veriyor. Hedef SADECE 127.0.0.1.
          html: mesaj.html || null
        })
      });
      const t = await r.text();
      cevapla({ sonuc: t.slice(0, 120) });
    } catch (e) {
      console.log("[oto-kelepir] sunucu hatasi:", String(e).slice(0, 120));
      cevapla({ sonuc: "hata: " + String(e).slice(0, 80) });
    }
  })();
  return true;    // asenkron cevap
});

// ── 2) BEKCI ────────────────────────────────────────────────────────
async function bekciTuru() {
  const simdi = Math.floor(Date.now() / 1000);
  let sekmeler = [];
  try {
    sekmeler = await chrome.tabs.query({ url: LISTE_DESEN });
  } catch (e) {
    sekmeler = [];
  }
  const depo = await chrome.storage.local.get(["sonVeri", "sonYenileme"]);
  let sonVeri = depo.sonVeri || 0;
  const sonYenileme = depo.sonYenileme || 0;
  // Hic veri gorulmediyse sayaci SIMDI baslat; yoksa sessizlik hic
  // olculemez ve bekci sonsuza kadar "izliyor" der (08.10 hatasi).
  if (!sonVeri) {
    await chrome.storage.local.set({ sonVeri: simdi });
    sonVeri = simdi;
  }
  let sessizSn = simdi - sonVeri;
  const donmus = sekmeler.filter((t) => t.discarded).length;

  // SUNUCUYA SOR: veri gercekten geliyor mu? Veri yolu dogrudan POST'a
  // tasindiginda bu worker artik mesaj gormuyor, bu yuzden kendi kaydi
  // yanlistir. Sunucunun cevabi varsa O gecerlidir.
  let kaynak = "yerel";
  try {
    const r = await fetch(DURUM, { cache: "no-store" });
    if (r.ok) {
      const d = await r.json();
      if (d && d.son_gorulme) {
        const yas = Math.floor(
          (Date.now() - new Date(d.son_gorulme).getTime()) / 1000);
        if (yas >= 0) {
          sessizSn = yas;
          kaynak = "sunucu";
        }
      }
    }
  } catch (e) { /* sunucu kapali -> yerel kayit ile devam */ }

  let eylem = "izliyor";
  // Sekme var ama uzun suredir veri yok -> icindeki toplayici olmus.
  // En yaygin sebep tarayicinin sekmeyi DONDURMASI; bu halde
  // `discarded` false kalir, o yuzden ona BAKMIYORUZ.
  if (sekmeler.length && sessizSn > SESSIZ_ESIK &&
      simdi - sonYenileme > YENILEME_ARASI) {
    const hedef = sekmeler.find((t) => t.discarded) || sekmeler[0];
    try {
      await chrome.tabs.reload(hedef.id);
      await chrome.storage.local.set({
        sonYenileme: simdi, sonVeri: simdi
      });
      eylem = "sekme yenilendi (" + sessizSn + " sn sessizdi)";
      console.log("[oto-kelepir] bekci:", eylem);
    } catch (e) {
      eylem = "yenileme hatasi: " + String(e).slice(0, 60);
    }
  } else if (!sekmeler.length) {
    eylem = "sekme YOK";
  }

  // Panele bildir — sekme olmese bile uzantinin yasadigi buradan belli.
  try {
    await fetch(NABIZ, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sekme: sekmeler.length, donmus: donmus,
        sessiz_sn: sessizSn, olcum: kaynak, eylem: eylem
      })
    });
  } catch (e) { /* sunucu kapaliysa sessiz gec */ }
}

// ── 3) TEMPO (08.10.2026) ───────────────────────────────────────────
// OLCUM: arkadaki sekmede sayfa zamanlayicisi 50-80 sn'ye ayarliyken
// turlar 118 sn arayla dustu. Sebep tarayicinin arka plan sekmelerinde
// zamanlayicilari dakikada bire kisip dakika sinirlarina hizalamasi.
// Servis calisaninin alarmi bu kisitlamaya tabi DEGIL, bu yuzden tempo
// buraya tasindi: dakikada bir "tur" denir, sayfa 0-15 sn rastgele
// gecikme ekler. Sayfa kendi zamanlayicisini YEDEK olarak surduruyor.
async function turIstegiYolla() {
  let sekmeler = [];
  try {
    sekmeler = await chrome.tabs.query({ url: LISTE_DESEN });
  } catch (e) {
    return;
  }
  for (const t of sekmeler) {
    if (t.discarded) continue;            // donmus sekme mesaj islemez
    try {
      await chrome.tabs.sendMessage(t.id, { tip: "tur_istegi" });
    } catch (e) { /* content script yok/yeni yuklenmis — sorun degil */ }
  }
}

function alarmiKur() {
  chrome.alarms.create("bekci", { periodInMinutes: 1 });
}

alarmiKur();
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name !== "bekci") return;
  turIstegiYolla();      // tempo
  bekciTuru();           // saglik kontrolu
});
chrome.runtime.onStartup.addListener(alarmiKur);
chrome.runtime.onInstalled.addListener(() => {
  alarmiKur();
  console.log("[oto-kelepir] uzanti kuruldu — yerel sunucu:", SUNUCU);
});
