// スマホ内の保存（IndexedDB）。サーバーには何も送らない。
//   papers   id → {id, meta, paper}           論文（章と文）
//   audio    "<id>/<音声id>" → Blob            1文ずつの音声（m4a）
//   figures  "<id>/<file>" → Blob              図・表・数式の画像（png）。一覧は papers の figures
//   words    headword → {headword, meaning, source, first_seen, examples[], origin}
//   reviews  uid → {uid, headword, rating, reviewed_at, device}   答えの記録（Mac のものも含む）
//   lookups  uid → スマホで引いた記録（Mac に戻す）
//   kv       設定など（device, last_export_at, ...）
const NAME = "paper_reader";
const STORES = ["papers", "audio", "figures", "words", "reviews", "lookups", "kv"];
let dbp = null;

function open() {
  if (dbp) return dbp;
  dbp = new Promise((resolve, reject) => {
    const req = indexedDB.open(NAME, 2);           // 2: figures を足した（前からある保存はそのまま）
    req.onupgradeneeded = () => {
      const db = req.result;
      for (const s of STORES) if (!db.objectStoreNames.contains(s)) db.createObjectStore(s);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbp;
}

function wrap(req) {
  return new Promise((resolve, reject) => { req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error); });
}

export async function get(store, key) {
  const db = await open();
  return wrap(db.transaction(store).objectStore(store).get(key));
}

export async function all(store) {
  const db = await open();
  return wrap(db.transaction(store).objectStore(store).getAll());
}

export async function keys(store) {
  const db = await open();
  return wrap(db.transaction(store).objectStore(store).getAllKeys());
}

export async function put(store, key, value) {
  const db = await open();
  const tx = db.transaction(store, "readwrite");
  tx.objectStore(store).put(value, key);
  return new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
}

// [[store, key, value], ...] を1つの書き込みでまとめて入れる
export async function putMany(entries) {
  const db = await open();
  const names = [...new Set(entries.map((e) => e[0]))];
  if (!names.length) return;
  const tx = db.transaction(names, "readwrite");
  for (const [s, k, v] of entries) tx.objectStore(s).put(v, k);
  return new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
}

export async function del(store, key) {
  const db = await open();
  const tx = db.transaction(store, "readwrite");
  tx.objectStore(store).delete(key);
  return new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
}

export async function clearAll() {
  const db = await open();
  const tx = db.transaction(STORES, "readwrite");
  for (const s of STORES) tx.objectStore(s).clear();
  return new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
}
