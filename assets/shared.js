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

// ---- 復習の「空欄を埋める」モード ----
function stems(w) {
  w = String(w || "").toLowerCase().trim();
  const out = new Set([w]);
  const add = (x) => { if (x && x.length >= 2) out.add(x); };
  if (w.endsWith("ies")) add(w.slice(0, -3) + "y");
  if (w.endsWith("es")) add(w.slice(0, -2));
  if (w.endsWith("s") && !w.endsWith("ss")) add(w.slice(0, -1));
  if (w.endsWith("ied")) add(w.slice(0, -3) + "y");
  for (const suf of ["ed", "ing"]) {
    if (!w.endsWith(suf)) continue;
    const b = w.slice(0, -suf.length);
    add(b); add(b + "e");
    if (b.length > 2 && b.at(-1) === b.at(-2)) add(b.slice(0, -1));   // stopped → stop
  }
  return out;
}

function editDistance(a, b) {
  a = a.toLowerCase(); b = b.toLowerCase();
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++)
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[a.length][b.length];
}

// 例文の中の、その単語の出てくる所（引いたときの形 query を優先し、無ければ見出し語の活用形）→ {before, answer, after}
export function clozeParts(sentence, headword, query) {
  if (!sentence || !headword) return null;
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const tries = [];
  if (query) tries.push(new RegExp(`(?<![A-Za-z])(${esc(query)})(?![A-Za-z])`, "i"));
  tries.push(new RegExp(`(?<![A-Za-z])(${esc(headword)}(?:s|es|d|ed|ing)?)(?![A-Za-z])`, "i"));
  const stem = headword.length > 4 ? headword.replace(/(e|y)$/i, "") : headword;
  tries.push(new RegExp(`(?<![A-Za-z])(${esc(stem)}[a-z]{0,4})(?![A-Za-z])`, "i"));
  for (const re of tries) {
    const m = sentence.match(re);
    if (m) return { before: sentence.slice(0, m.index), answer: m[1], after: sentence.slice(m.index + m[1].length) };
  }
  return null;
}

// 入力の判定: exact（文のとおり）/ form（見出し語・別の活用形）/ near（1字違い。長い語は2字まで）/ wrong
export function checkCloze(input, answer, headword) {
  const x = String(input || "").trim().toLowerCase();
  if (!x) return "wrong";
  if (x === answer.toLowerCase()) return "exact";
  const target = new Set([...stems(answer), ...stems(headword)]);
  if ([...stems(x)].some((s) => target.has(s))) return "form";
  const lim = answer.length >= 8 ? 2 : 1;
  return [answer, headword].some((t) => editDistance(x, t) <= lim) ? "near" : "wrong";
}

// 勧める答え: 間違い・わからない → 1、ヒントを使った・惜しい → 2、正解 → 3
export function suggestRating(result, usedHint) {
  if (result === "wrong") return 1;
  if (result === "near" || usedHint) return 2;
  return 3;
}

// どの例文で出すか: 空欄を作れる文のうち、訳のあるものを先に。答えた回数で順番に替える。無ければ null
const CITE = /\s*\((?=[^()]*\b(?:19|20)\d{2}[a-z]?\b)[^()]*\)|\s*\[\d+(?:\s*[-–,]\s*\d+)*\]|(?<=[A-Za-z.,;)])[¹²³⁴⁵⁶⁷⁸⁹⁰][⁰¹²³⁴⁵⁶⁷⁸⁹˒,–-]*/g;

// 問題に出す形: 引用（(Smith et al., 2020)・[12]・上付きの番号）を外し、長い文は空欄の前後 90 字ほどに縮める
function clozeText(parts) {
  const clean = (t) => t.replace(CITE, "");
  let before = clean(parts.before), after = clean(parts.after);
  if (before.length > 110) before = "… " + before.slice(before.length - 90).replace(/^\S*\s/, "");
  if (after.length > 110) after = after.slice(0, 90).replace(/\s\S*$/, "") + " …";
  return { before, answer: parts.answer, after };
}

export function pickExample(card) {
  const list = (card.examples || []).map((e) => {
    const parts = clozeParts(e.sentence, card.headword, e.query);
    return { ...e, parts: parts && clozeText(parts) };
  }).filter((e) => e.parts);
  if (!list.length) return null;
  const withJa = list.filter((e) => e.ja);
  const pool = withJa.length ? withJa : list;
  return pool[(card.reviews || 0) % pool.length];
}
