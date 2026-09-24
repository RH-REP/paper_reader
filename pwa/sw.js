// オフラインで動かすための Service Worker。
// - 画面とプログラム（html / js / css など）: つながれば必ず新しいものを取り、保存し直す。つながらなければ保存したもの
//   （保存したものを先に出すと、更新直後に「新しい画面＋古いプログラム」が混ざって動かなくなるため）
// - 辞書（dict/*.json）: 大きく、めったに変わらないので保存したものを先に出す
// 版を上げると古い保存は消える。新しい版に切り替わると、画面（js/app.js）が1回だけ読み直す。
const CACHE = "paper_reader-v8";
const SHELL = ["./", "index.html", "app.css", "manifest.webmanifest", "js/app.js", "js/db.js", "js/lookup.js",
               "js/srs.js", "js/zip.js", "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function networkFirst(req, key) {
  try {
    const r = await fetch(req, { cache: "no-cache" });
    if (r.ok) (await caches.open(CACHE)).put(key || req, r.clone());
    return r;
  } catch {
    return (await caches.match(key || req)) || Response.error();
  }
}

async function cacheFirst(req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok) (await caches.open(CACHE)).put(req, r.clone());
  return r;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;     // 英英 API などはそのまま
  if (req.mode === "navigate") return e.respondWith(networkFirst(req, "index.html"));
  if (url.pathname.includes("/dict/")) return e.respondWith(cacheFirst(req));
  e.respondWith(networkFirst(req));
});
