// ════════════════════════════════════════════════════════════════════
//  OTO KELEPIR — ARKA PLAN ISCISI (service worker)
//
//  IKI GOREVI VAR:
//
//  1) POSTACI — content script'ten gelen ilanlari yerel sunucuya
//     (127.0.0.1) yollar. Bu isi content script'ten DOGRUDAN yapmak
//     CORS'a takiliyor; service worker host_permissions ile yollayabilir.
//
//  2) BEKCI (08.10.2026) — dakikada bir kontrol eder: sahibinden liste
//     sekmesi duruyor mu, ondan veri geliyor mu? Tarayici bellek
//     tasarrufu icin arka plandaki sekmeyi DONDURUYOR (discard) ve o
//     anda sayfanin icindeki toplayici OLUYOR; sekme acik gorunur ama
//     hicbir sey yapmaz. OLCUM: tur sayaci iki kez 1'den basladi, 3
//     dakikada tek tur dustu. Bekci bu durumda sekmeyi bir kez yeniler
//     (kullanicinin F5'i ile ayni sey, kendi tarayicisinda) ve sonucu
//     panele bildirir. Boylece kullanici evde olmadan da sistem ayakta
//     kalir ve ayakta OLMADIGINDA bunu panelden gorur.
//
//  sahibinden'e veri istegi ATMAZ; tek istedigi yerel sunucu + gerekirse
//  var olan sekmenin yenilenmesi.
// ════════════════════════════════════════════════════════════════════

const SUNUCU = "http://127.0.0.1:8765/ilan";
const NABIZ = "http://127.0.0.1:8765/nabiz";
const LISTE_DESEN = "https://www.sahibinden.com/otomobil*";

const SESSIZ_ESIK = 300;     // sn — bu kadar veri yoksa sekme olmus say
const YENILEME_ARASI = 300;  // sn — iki yenileme arasi en az bu kadar

// ── 1) POSTACI ──────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((mesaj, _gonderen, cevapla) => {
  if (!mesaj || mesaj.tip !== "ilanlar") return false;
  // NABIZ: kartlar bos olsa bile yollanir, boylece sunucu "calisiyor ama
  // yeni ilan yok" halini gorur ("durmus" ile karismasin).
  const kartlar = mesaj.kartlar || [];
  chrome.storage.local.set({ sonVeri: Math.floor(Date.now() / 1000) });
  fetch(SUNUCU, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kaynak: "uzanti", kartlar: kartlar, ozet: mesaj.ozet || null,
      // Ham liste HTML'i — sunucu zengin semaya yazmak icin otobotun
      // ayristiricisina veriyor. Hedef SADECE 127.0.0.1.
      html: mesaj.html || null
    })
  })
    .then((r) => r.text())
    .then((t) => cevapla({ sonuc: t.slice(0, 120) }))
    .catch((e) => {
      console.log("[oto-kelepir] sunucu hatasi:", String(e).slice(0, 120));
      cevapla({ sonuc: "hata: " + String(e).slice(0, 80) });
    });
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
  const sonVeri = depo.sonVeri || 0;
  const sonYenileme = depo.sonYenileme || 0;
  const sessizSn = sonVeri ? simdi - sonVeri : null;
  const donmus = sekmeler.filter((t) => t.discarded).length;

  let eylem = "izliyor";
  // Sekme var ama uzun suredir veri yok -> icindeki toplayici olmus
  // (en yaygin sebep: tarayicinin sekmeyi dondurmesi). Bir kez yenile.
  if (sekmeler.length && sessizSn !== null && sessizSn > SESSIZ_ESIK &&
      simdi - sonYenileme > YENILEME_ARASI) {
    const hedef = sekmeler.find((t) => t.discarded) || sekmeler[0];
    try {
      await chrome.tabs.reload(hedef.id);
      await chrome.storage.local.set({ sonYenileme: simdi });
      eylem = "sekme yenilendi";
      console.log("[oto-kelepir] bekci: sekme yenilendi (" + sessizSn +
                  " sn veri yoktu)");
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
        sessiz_sn: sessizSn, eylem: eylem
      })
    });
  } catch (e) { /* sunucu kapaliysa sessiz gec */ }
}

chrome.alarms.create("bekci", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "bekci") bekciTuru();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("bekci", { periodInMinutes: 1 });
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("bekci", { periodInMinutes: 1 });
  console.log("[oto-kelepir] uzanti kuruldu — yerel sunucu:", SUNUCU);
});
