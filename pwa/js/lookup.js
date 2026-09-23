// 英単語を引く（Mac 版 lookup.py と同じ順）。
//   1. EJDict（dict/<頭文字>.json）をそのまま  2. 活用を戻す  3. 派生語を戻す（近い語）  4. ハイフンの部分
//   5. Free Dictionary API（英英。ネットがあるときだけ）
const shards = new Map();

async function shard(ch) {
  if (!shards.has(ch)) {
    shards.set(ch, fetch(`dict/${ch}.json`).then((r) => (r.ok ? r.json() : {})).catch(() => ({})));
  }
  return shards.get(ch);
}

async function ej(w) {
  if (!/^[a-z]/.test(w)) return [];
  const d = await shard(w[0]);
  return (d[w] || []).slice(0, 5).map(([word, mean]) => ({ word, mean }));
}

export function normalize(word) {
  return word.trim().replace(/^[.,;:!?()[\]{}"“”‘’']+|[.,;:!?()[\]{}"“”‘’']+$/g, "").toLowerCase().replace(/['’]s$/, "");
}

export function inflectionCandidates(w) {
  const c = [];
  const add = (x) => { if (x.length >= 2 && !c.includes(x) && x !== w) c.push(x); };
  if (w.endsWith("ies")) add(w.slice(0, -3) + "y");
  if (w.endsWith("es")) add(w.slice(0, -2));
  if (w.endsWith("s") && !w.endsWith("ss")) add(w.slice(0, -1));
  if (w.endsWith("ied")) add(w.slice(0, -3) + "y");
  if (w.endsWith("ed")) {
    add(w.slice(0, -2)); add(w.slice(0, -1));
    if (w.length > 4 && w.at(-3) === w.at(-4)) add(w.slice(0, -3));
  }
  if (w.endsWith("ing")) {
    add(w.slice(0, -3)); add(w.slice(0, -3) + "e");
    if (w.length > 5 && w.at(-4) === w.at(-5)) add(w.slice(0, -4));
  }
  if (w.endsWith("er")) { add(w.slice(0, -2)); add(w.slice(0, -1)); }
  if (w.endsWith("est")) { add(w.slice(0, -3)); add(w.slice(0, -2)); }
  if (w.endsWith("ly")) add(w.slice(0, -2));
  return c;
}

const DERIVATIONS = [["ability", ""], ["ability", "e"], ["able", ""], ["able", "e"], ["ible", ""], ["ible", "e"],
  ["ator", "ate"], ["ators", "ate"], ["or", ""], ["ors", ""], ["ation", "ate"], ["ation", "e"], ["ations", "ate"],
  ["ation", ""], ["ment", ""], ["ments", ""], ["ness", ""], ["ity", ""], ["ity", "e"], ["ization", "ize"],
  ["ized", "ize"], ["ically", "ic"], ["ically", "ical"]];

export function derivationCandidates(w) {
  const out = [];
  for (const [suf, rep] of DERIVATIONS) {
    if (w.endsWith(suf) && w.length - suf.length >= 3) {
      const x = w.slice(0, -suf.length) + rep;
      if (!out.includes(x) && x !== w) out.push(x);
    }
  }
  return out;
}

async function api(w) {
  if (!navigator.onLine) return [];
  try {
    const r = await fetch(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(w)}`);
    if (!r.ok) return [];
    const e = (await r.json())[0];
    const parts = (e.meanings || []).slice(0, 3).map((m) =>
      `(${m.partOfSpeech || ""}) ` + (m.definitions || []).slice(0, 2).map((d) => d.definition).join(" / "));
    return parts.length ? [{ word: e.word || w, mean: parts.join(" / ") }] : [];
  } catch { return []; }
}

export async function lookup(word) {
  const q = normalize(word);
  const res = { query: word, normalized: q, found: false, source: null, entries: [], note: null,
                weblio: `https://ejje.weblio.jp/content/${encodeURIComponent(q)}` };
  if (!q || !/^[a-z][a-z\-']*$/.test(q)) return res;
  const tries = [[q, null], ...inflectionCandidates(q).map((c) => [c, "原形"]), ...derivationCandidates(q).map((c) => [c, "近い語"])];
  for (const [cand, note] of tries) {
    const hits = await ej(cand);
    if (hits.length) return { ...res, found: true, source: "EJDict", headword: hits[0].word, entries: hits, note };
  }
  if (q.includes("-")) {
    for (const part of q.split("-").reverse()) {
      const hits = await ej(part);
      if (hits.length) return { ...res, found: true, source: "EJDict", headword: hits[0].word, entries: hits, note: "部分" };
    }
  }
  const hits = await api(q);
  if (hits.length) return { ...res, found: true, source: "Free Dictionary API（英英）", headword: hits[0].word, entries: hits };
  return res;
}
