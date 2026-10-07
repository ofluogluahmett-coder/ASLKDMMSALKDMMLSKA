// ════════════════════════════════════════════════════════════════════
//  OTO KELEPIR — ARKA PLAN ISCISI (service worker)
//
//  Gorev: content script'ten gelen ilanlari yerel sunucuya (127.0.0.1)
//  yollamak. Bu isi content script'ten DOGRUDAN yapmak CORS'a takiliyor;
//  service worker host_permissions ile yollayabiliyor.
//
//  sahibinden'e HICBIR istek atmaz — tek isi yerel sunucuya POST.
// ════════════════════════════════════════════════════════════════════

const SUNUCU = "http://127.0.0.1:8765/ilan";

chrome.runtime.onMessage.addListener((mesaj, _gonderen, cevapla) => {
  if (!mesaj || mesaj.tip !== "ilanlar") return false;
  const kartlar = mesaj.kartlar || [];
  if (!kartlar.length) {
    cevapla({ sonuc: "bos" });
    return false;
  }
  fetch(SUNUCU, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kaynak: "uzanti", kartlar: kartlar })
  })
    .then((r) => r.text())
    .then((t) => cevapla({ sonuc: t.slice(0, 120) }))
    .catch((e) => {
      console.log("[oto-kelepir] sunucu hatasi:", String(e).slice(0, 120));
      cevapla({ sonuc: "hata: " + String(e).slice(0, 80) });
    });
  return true;    // asenkron cevap
});

chrome.runtime.onInstalled.addListener(() => {
  console.log("[oto-kelepir] uzanti kuruldu — yerel sunucu:", SUNUCU);
});
