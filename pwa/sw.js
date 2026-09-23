// オフラインで動かすための Service Worker。
// 画面のファイルは入れたときにまとめて保存し、辞書（dict/*.json）は一度読んだら保存する。
// 画面を直したら CACHE の版を上げる（古い保存は消える）。
const CACHE = "paper_reader-v1";
const SHELL = ["./", "index.html", "app.css", "manifest.webmanifest", "js/app.js", "js/db.js", "js/lookup.js",
               "js/srs.js", "js/zip.js", "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;     // 英英 API などはそのまま
  if (req.mode === "navigate") {
    // 画面は、つながれば新しいもの、だめなら保存したもの
    e.respondWith(fetch(req).then((r) => { const c = r.clone(); caches.open(CACHE).then((x) => x.put("index.html", c)); return r; })
      .catch(() => caches.match("index.html")));
    return;
  }
  e.respondWith(caches.match(req).then((hit) => {
    const net = fetch(req).then((r) => {
      if (r.ok) { const c = r.clone(); caches.open(CACHE).then((x) => x.put(req, c)); }
      return r;
    }).catch(() => hit || Response.error());
    // 辞書は保存したものを優先。画面のファイルは保存したものを出しつつ裏で新しくする
    return hit || net;
  }));
});
