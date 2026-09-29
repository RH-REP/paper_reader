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

// ---- ディクテーション（聞き取って空欄に打つ）----
const STOP = new Set(("a an the of to in on at by for from with and or but as is are was were be been being it its this that these those " +
  "we our they their he she his her which who whom whose what when where how than then so such not no can could may might " +
  "will would shall should must do does did has have had into onto over under about also there here if while both each").split(" "));

// 文 → [{t, word}]（word は空欄にできる英語の語。数字・記号・空白は word でない）
export function tokenize(sentence) {
  const out = [];
  for (const part of String(sentence).split(/([A-Za-z](?:[A-Za-z'’\-]*[A-Za-z])?)/)) {
    if (part) out.push({ t: part, word: /^[A-Za-z]/.test(part) });
  }
  return out;
}

// レベルごとの空欄（どれもランダム。用語集などの生成物には頼らない）:
//   1 = 内容語から1語、2 = 内容語の約2割、3 = 約4割、4 = 内容語すべて、5 = 文全体
// 返り値は空欄にするトークンの番号の Set。rand は 0〜1 を返す関数（テストで固定できる）
export function pickBlanks(tokens, level, { rand = Math.random } = {}) {
  const words = tokens.map((x, i) => ({ ...x, i })).filter((x) => x.word);
  if (level >= 5) return new Set(words.map((x) => x.i));
  const isAcr = (t) => /^[A-Z0-9]{2,}s?$/.test(t);
  const content = words.filter((x) => !STOP.has(x.t.toLowerCase()) && x.t.length >= 3 && (level >= 4 || !isAcr(x.t)));
  const pool = content.length ? content : words;
  if (!pool.length) return new Set();
  if (level >= 4) return new Set(pool.map((x) => x.i));
  const shuffled = [...pool];
  for (let k = shuffled.length - 1; k > 0; k--) {       // 偏りの無い並べ替え（Fisher–Yates）
    const r = Math.floor(rand() * (k + 1));
    [shuffled[k], shuffled[r]] = [shuffled[r], shuffled[k]];
  }
  if (level <= 1) return new Set([shuffled[0].i]);
  const n = Math.max(level === 2 ? 1 : 2, Math.round(pool.length * (level === 2 ? 0.2 : 0.4)));
  return new Set(shuffled.slice(0, n).map((x) => x.i));
}

// 1語の判定: ok（大文字小文字を除いて一致）/ near（1字違い・別の活用形）/ ng
export function gradeWord(typed, answer) {
  const x = String(typed || "").trim().replace(/[’]/g, "'").toLowerCase(), a = answer.replace(/[’]/g, "'").toLowerCase();
  if (!x) return "ng";
  if (x === a) return "ok";
  const r = checkCloze(x, answer, answer);
  return r === "wrong" ? "ng" : "near";
}

// 文全体のディクテーション: 打った文と元の文を語の並びで突き合わせる（最長共通部分列）。
// 返り値 {words: [{t, r: ok|near|ng}], extra: [打ったが元に無い語], score: 0〜1}
export function gradeSentence(typed, sentence) {
  const orig = tokenize(sentence).filter((x) => x.word).map((x) => x.t);
  const got = tokenize(typed).filter((x) => x.word).map((x) => x.t);
  const n = orig.length, m = got.length;
  const eq = (a, b) => gradeWord(b, a) !== "ng";
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = eq(orig[i], got[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const words = [], extra = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (eq(orig[i], got[j])) { words.push({ t: orig[i], r: gradeWord(got[j], orig[i]) }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) { words.push({ t: orig[i], r: "ng" }); i++; }
    else { extra.push(got[j]); j++; }
  }
  while (i < n) words.push({ t: orig[i++], r: "ng" });
  while (j < m) extra.push(got[j++]);
  const pts = words.reduce((s, w) => s + (w.r === "ok" ? 1 : w.r === "near" ? 0.5 : 0), 0);
  return { words, extra, score: n ? pts / n : 0 };
}

// 直近の正解率からレベルの案内（直近 20 文、10 文以上たまってから）: 9 割超なら上げる、5 割未満なら下げる（決めるのは人）
export function levelAdvice(history, level) {
  const recent = history.filter((h) => h.level === level).slice(-20);
  if (recent.length < 10) return { rate: recent.length ? recent.reduce((s, h) => s + h.score, 0) / recent.length : null, n: recent.length, advice: null };
  const rate = recent.reduce((s, h) => s + h.score, 0) / recent.length;
  return { rate, n: recent.length, advice: rate > 0.9 && level < 5 ? "up" : rate < 0.5 && level > 1 ? "down" : null };
}

// ---- 図を大きく開いたときの拡大・移動（ホイール・トラックパッドのピンチ・2本指のピンチ・ダブルクリック／ダブルタップ・ドラッグ）----
// stage: 画像を入れる枠（overflow: hidden）、img: 画像。返り値の reset() で画面に収まる大きさに戻す。scale() で今の倍率
export function attachZoom(stage, img, { onSwipe } = {}) {
  let s = 1, x = 0, y = 0;
  const ptrs = new Map();
  let start = null, lastTap = 0, moved = false;
  const apply = () => { img.style.transform = `translate(${x}px, ${y}px) scale(${s})`; stage.classList.toggle("zoomed", s > 1.01); };
  const clamp = () => {
    if (s <= 1.01) { s = 1; x = 0; y = 0; return; }
    const r = stage.getBoundingClientRect(), w = img.offsetWidth * s, h = img.offsetHeight * s;
    const mx = Math.max(0, (w - r.width) / 2), my = Math.max(0, (h - r.height) / 2);
    x = Math.min(mx, Math.max(-mx, x));
    y = Math.min(my, Math.max(-my, y));
  };
  const zoomAt = (factor, cx, cy) => {                // (cx, cy) の点を動かさずに拡大・縮小
    const r = stage.getBoundingClientRect();
    const px = cx - (r.left + r.width / 2), py = cy - (r.top + r.height / 2);
    const ns = Math.min(8, Math.max(1, s * factor));
    x = px - (px - x) * (ns / s);
    y = py - (py - y) * (ns / s);
    s = ns;
    clamp();
    apply();
  };
  stage.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0025)), e.clientX, e.clientY); }, { passive: false });
  stage.addEventListener("dblclick", (e) => { s > 1.01 ? reset() : zoomAt(2.5, e.clientX, e.clientY); });
  stage.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button, a")) return;       // 前・次のボタンは押せるままにする
    stage.setPointerCapture(e.pointerId);
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved = false;
    start = { x: e.clientX, y: e.clientY, t: Date.now() };
  });
  stage.addEventListener("pointermove", (e) => {
    if (!ptrs.has(e.pointerId)) return;
    const prev = ptrs.get(e.pointerId);
    if (ptrs.size === 2) {                          // 2本指のピンチ
      const [a, b] = [...ptrs.values()];
      const d0 = Math.hypot(a.x - b.x, a.y - b.y);
      ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const [c, d] = [...ptrs.values()];
      const d1 = Math.hypot(c.x - d.x, c.y - d.y);
      if (d0 > 0) zoomAt(d1 / d0, (c.x + d.x) / 2, (c.y + d.y) / 2);
      moved = true;
      return;
    }
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) > 6) moved = true;
    if (s > 1.01) { x += e.clientX - prev.x; y += e.clientY - prev.y; clamp(); apply(); }
  });
  const up = (e) => {
    if (!ptrs.has(e.pointerId)) return;
    ptrs.delete(e.pointerId);
    if (ptrs.size || !start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (s <= 1.01 && moved && Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5 && onSwipe) onSwipe(dx < 0 ? 1 : -1);   // 等倍のときだけ、はらって前後へ
    if (!moved && e.pointerType === "touch") {      // ダブルタップ
      if (Date.now() - lastTap < 300) { s > 1.01 ? reset() : zoomAt(2.5, e.clientX, e.clientY); lastTap = 0; }
      else lastTap = Date.now();
    }
    start = null;
  };
  stage.addEventListener("pointerup", up);
  stage.addEventListener("pointercancel", up);
  function reset() { s = 1; x = 0; y = 0; apply(); }
  return { reset, scale: () => s, zoomBy: (f) => { const r = stage.getBoundingClientRect(); zoomAt(f, r.left + r.width / 2, r.top + r.height / 2); } };
}

// ---- 理解クイズ（quiz.json。章の title で章と結び付ける）----
// その節を含む章（段 1）の title
export function chapterTitle(sections, sec) {
  let i = sections.indexOf(sec);
  while (i > 0 && sections[i].level > 1) i--;
  return i >= 0 ? sections[i].title : null;
}
// 本文の文（t）→ 音声 id（"<sec>_<k>"）。無ければ null
export function findSentenceId(sections, t) {
  for (const sec of sections) {
    const k = sec.sentences.findIndex((x) => x.t === t);
    if (k >= 0) return `${sec.id}_${k + 1}`;
  }
  return null;
}
export function quizChapter(quiz, title) {
  return (quiz?.chapters || []).find((c) => c.chapter === title && (c.questions || []).length) || null;
}
