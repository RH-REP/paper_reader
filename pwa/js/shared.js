// Mac の画面とスマホ版（PWA）で同じ動きをさせる部品。
// 正本は assets/shared.js。スマホ版は pwa/js/shared.js に同じものを置く（tests/test_shared_js.py で同じか確かめる）。

// ---- 本文の「Fig. 3」「Figures 2 and 4」「Table 1」「Eq. (3)」を図の一覧と結び付ける ----
// 図の一覧の label（"Fig. 3" "FIGURE 2" "Table 1" "Eq. (3)" "Fig. A-1"）も本文の書き方も、同じ鍵（"figure:3"）にそろえる
const KIND_WORDS = [
  [/^fig(?:ure)?s?\.?$/i, "figure"],
  [/^(?:table|tab)s?\.?$/i, "table"],
  [/^eq(?:uation)?s?\.?$/i, "equation"],
];
const NUM = "(?:[A-Z]-?\\d+|\\d+)(?:\\([a-z]\\)|[a-z](?![A-Za-z]))?";   // 3、3a、3(a)、A-1
const REF_RE = new RegExp(
  `\\b(Fig(?:ure)?s?\\.?|FIG(?:URE)?S?\\.?|Tables?|TABLES?|Tabs?\\.|Eq(?:uation)?s?\\.?)\\s*` +
  `(\\(?${NUM}\\)?(?:\\s*(?:,|and|&|–|-|to)\\s*\\(?${NUM}\\)?)*)`, "g");

function kindOf(word) {
  for (const [re, k] of KIND_WORDS) if (re.test(word)) return k;
  return null;
}

function numKey(n) {
  const m = String(n).match(/[A-Z]-?\d+|\d+/);
  return m ? m[0].replace(/^([A-Z])(\d)/, "$1-$2") : null;       // "A1" と "A-1" を同じにする
}

// 図の一覧の label → 鍵。わからなければ null
export function labelKey(label) {
  const m = String(label || "").trim().match(/^([A-Za-z]+\.?)\s*\(?([A-Z]?-?\d+)/);
  if (!m) return null;
  const k = kindOf(m[1]);
  const n = numKey(m[2]);
  return k && n ? `${k}:${n}` : null;
}

// 文 → [{text}, {text, key}] の並び。key のあるものが図への参照（番号ごとに分ける。「Figs. 3 and 4」なら 3 と 4）
export function splitRefs(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(REF_RE)) {
    const kind = kindOf(m[1].replace(/\s+$/, ""));
    if (!kind) continue;
    if (m.index > last) out.push({ text: text.slice(last, m.index) });
    out.push({ text: m[1] + text.slice(m.index + m[1].length, m.index + m[0].length - m[2].length) });
    let pos = 0;
    const list = m[2];
    for (const n of list.matchAll(new RegExp(NUM, "g"))) {
      if (n.index > pos) out.push({ text: list.slice(pos, n.index) });
      out.push({ text: n[0], key: `${kind}:${numKey(n[0])}` });
      pos = n.index + n[0].length;
    }
    if (pos < list.length) out.push({ text: list.slice(pos) });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}

// 文の中の図への参照の鍵（重なりなし、出てきた順）
export function refKeys(text) {
  return [...new Set(splitRefs(text).filter((p) => p.key).map((p) => p.key))];
}

// ---- 文を、クリックできる単語と図への参照に分けて入れる ----
// 単語は <span class="w">、図への参照は <a class="fr" data-key>（押すと図を開く）、用語集にある語は class に "term" を足す
export function fillWords(el, text, opts = {}) {
  el.textContent = "";
  const terms = opts.terms;                          // Set（小文字の語）
  for (const seg of splitRefs(text)) {
    if (seg.key) {
      const a = document.createElement("a");
      a.className = "fr";
      a.dataset.key = seg.key;
      a.textContent = seg.text;
      a.title = "図を開く";
      el.appendChild(a);
      continue;
    }
    for (const part of seg.text.split(/([A-Za-z](?:[A-Za-z'’\-]*[A-Za-z])?)/)) {
      if (!part) continue;
      if (/^[A-Za-z]/.test(part)) {
        const s = document.createElement("span");
        s.className = terms && terms.has(part.toLowerCase()) ? "w term" : "w";
        s.textContent = part;
        el.appendChild(s);
      } else {
        el.appendChild(document.createTextNode(part));
      }
    }
  }
}

// ---- しおり: 保存した位置（文の中身）を、今の読み上げの並びの何番目かに戻す ----
// 章の編集や AI の手直しで番号がずれても、同じ文があればそこへ。無ければ音声 id、それも無ければ最初から
export function resumeIndex(items, pos) {
  if (!pos || !items.length) return { index: 0, at: 0, exact: false };
  let i = items.findIndex((it) => it.show === pos.sentence && (!!it.heading === String(pos.item_id || "").endsWith("_h")));
  if (i < 0) i = items.findIndex((it) => it.show === pos.sentence);
  if (i >= 0) return { index: i, at: Number(pos.offset) || 0, exact: true };
  i = items.findIndex((it) => it.id === pos.item_id);
  return { index: Math.max(0, i), at: 0, exact: false };
}
