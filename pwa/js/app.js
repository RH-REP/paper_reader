// paper_reader スマホ版（PWA）。Mac で作った論文・音声・単語帳を読み込み、聞く・引く・復習する。
// データはこのスマホの IndexedDB にだけ置く。Mac とは zip（Mac → スマホ）と JSON（スマホ → Mac）で受け渡す。
import * as db from "./db.js";
import { lookup } from "./lookup.js";
import * as srs from "./srs.js";
import { unzipStored, text } from "./zip.js";

const VERSION = "8";
const $ = (id) => document.getElementById(id);
const S = { view: "read", papers: [], paper: null, items: [], pos: 0, playing: false, paused: false, gen: 0,
            player: new Audio(), wordAudio: new Audio(), url: null, review: null, device: null };

// ---- 共通 ----
function uid() { return crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : Math.random().toString(16).slice(2) + Date.now().toString(16); }
function fillWords(el, t) {
  el.textContent = "";
  for (const part of t.split(/([A-Za-z](?:[A-Za-z'’\-]*[A-Za-z])?)/)) {
    if (!part) continue;
    if (/^[A-Za-z]/.test(part)) { const s = document.createElement("span"); s.className = "w"; s.textContent = part; el.appendChild(s); }
    else el.appendChild(document.createTextNode(part));
  }
}
function markWord(t, head) {
  const el = document.createElement("span");
  const re = new RegExp(`\\b(${head.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\w*)`, "i");
  t.split(re).forEach((p, i) => {
    if (i % 2) { const m = document.createElement("mark"); m.textContent = p; el.appendChild(m); }
    else el.appendChild(document.createTextNode(p));
  });
  return el;
}
async function kv(key, value) {
  if (value === undefined) return db.get("kv", key);
  return db.put("kv", key, value);
}

// ---- 画面の切り替え（#read #review #words #data #help）----
function route() {
  const v = (location.hash || "#read").slice(1);
  S.view = ["read", "review", "words", "data", "help"].includes(v) ? v : "read";
  document.querySelectorAll(".view").forEach((s) => (s.hidden = s.id !== `v-${S.view}`));
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.v === S.view));
  $("sheet").hidden = true;
  if (S.view === "review") loadReview();
  if (S.view === "words") loadWords();
  if (S.view === "data") loadStats();
  window.scrollTo(0, 0);
}
addEventListener("hashchange", route);

// ---- 論文 ----
async function loadPapers() {
  S.papers = (await db.all("papers")).sort((a, b) => (a.meta.imported_at < b.meta.imported_at ? 1 : -1));
  const ul = $("paperList");
  ul.innerHTML = "";
  $("paperEmpty").hidden = S.papers.length > 0;
  for (const p of S.papers) {
    const li = document.createElement("li");
    li.textContent = p.meta.title;
    const sm = document.createElement("small");
    sm.textContent = (p.meta.source === "text" ? "テキスト" : `${p.meta.pages}ページ`) + `・${p.meta.sections}章・${p.meta.sentences}文`;
    li.appendChild(sm);
    li.onclick = () => openPaper(p.id);
    ul.appendChild(li);
  }
}

async function openPaper(id) {
  const p = await db.get("papers", id);
  if (!p) return;
  S.paper = p;
  await kv("last_paper", id);
  $("paperPick").hidden = true;
  $("paperView").hidden = false;
  $("paperTitle").textContent = p.meta.title;
  renderFigures(p);
  const ol = $("sections");
  ol.innerHTML = "";
  for (const sec of p.paper.sections) {
    const li = document.createElement("li");
    li.className = `sec l${Math.min(sec.level, 3)}` + (sec.kind === "back" ? " back" : "");
    li.dataset.sec = sec.id;
    const head = document.createElement("div");
    head.className = "head";
    const b = document.createElement("button");
    b.className = "play";
    b.textContent = "▶";
    b.onclick = () => play(itemsFor(sec.id));
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = sec.title;
    const c = document.createElement("span");
    c.className = "count";
    c.textContent = `${sec.sentences.length}文`;
    head.append(b, name, c);
    li.appendChild(head);
    if (sec.sentences.length) {
      const det = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = "文を見る（単語をタップで辞書）";
      const list = document.createElement("ol");
      sec.sentences.forEach((s, k) => {
        const x = document.createElement("li");
        x.dataset.t = s.t;                             // 辞書に渡す文（▶ の文字を混ぜない）
        if (s.s) {
          const b = document.createElement("button");
          b.className = "sp";
          b.textContent = "▶";
          b.title = "この文だけ再生";
          b.onclick = () => play([sentenceItem(sec, k)]);
          x.appendChild(b);
        }
        const t = document.createElement("span");
        fillWords(t, s.t);
        x.appendChild(t);
        const ja = p.ja?.[`${sec.id}_${k + 1}`];
        if (ja) { const j = document.createElement("div"); j.className = "ja"; j.textContent = ja; x.appendChild(j); }
        list.appendChild(x);
      });
      det.append(sum, list);
      li.appendChild(det);
    }
    ol.appendChild(li);
  }
}
// ---- 図・表・数式（画像として開く）----
const figUrls = [];
async function renderFigures(p) {
  figUrls.splice(0).forEach((u) => URL.revokeObjectURL(u));
  const figs = p.figures || [];
  $("figBtn").hidden = !figs.length;
  $("figBtn").textContent = `図・表・数式（${figs.length}）`;
  $("figPanel").hidden = true;
  const grid = $("figGrid");
  grid.textContent = "";
  S.figs = [];
  for (const [i, f] of figs.entries()) {
    const blob = await db.get("figures", `${p.id}/${f.file}`);
    if (!blob) continue;
    const url = URL.createObjectURL(blob);
    figUrls.push(url);
    S.figs.push({ ...f, url });
    const fig = document.createElement("figure");
    const img = document.createElement("img");
    img.src = url;
    img.loading = "lazy";
    img.alt = f.label || f.file;
    const cap = document.createElement("figcaption");
    cap.textContent = `${f.label || "画像"} ・ p.${f.page}`;
    fig.append(img, cap);
    const k = S.figs.length - 1;
    fig.onclick = () => openLightbox(k);
    grid.appendChild(fig);
  }
  $("figBtn").hidden = !S.figs.length;
  $("figBtn").textContent = `図・表・数式（${S.figs.length}）`;
}
$("figBtn").onclick = () => { $("figPanel").hidden = !$("figPanel").hidden; };
function openLightbox(i) {
  const n = S.figs.length;
  if (!n) return;
  S.lb = (i + n) % n;
  const f = S.figs[S.lb];
  $("lbImg").src = f.url;
  $("lbLabel").textContent = f.label || "画像";
  $("lbCount").textContent = `${S.lb + 1} / ${n} ・ p.${f.page}`;
  $("lbCaption").textContent = f.caption || "";
  $("lbOpen").href = f.url;
  $("lbOpen").download = f.file;
  $("lightbox").hidden = false;
}
$("lbPrev").onclick = () => openLightbox(S.lb - 1);
$("lbNext").onclick = () => openLightbox(S.lb + 1);
$("lbClose").onclick = () => { $("lightbox").hidden = true; };
// 左右にはらって前後の図へ
let touchX = null;
$("lightbox").addEventListener("touchstart", (e) => { touchX = e.touches[0].clientX; }, { passive: true });
$("lightbox").addEventListener("touchend", (e) => {
  if (touchX === null) return;
  const dx = e.changedTouches[0].clientX - touchX;
  touchX = null;
  if (Math.abs(dx) > 50) openLightbox(S.lb + (dx < 0 ? 1 : -1));
});

$("backToList").onclick = () => { $("paperPick").hidden = false; $("paperView").hidden = true; };

// ---- 読み上げ（1文ずつの m4a を順に鳴らす）----
function itemsFor(secId) {
  const secs = S.paper.paper.sections;
  let pick;
  if (!secId) pick = secs.filter((s) => !($("skipBack").checked && s.kind === "back"));
  else {
    const i = secs.findIndex((s) => s.id === secId);
    let j = i + 1;
    while (j < secs.length && secs[j].level > secs[i].level) j++;
    pick = secs.slice(i, j);
  }
  const items = [];
  for (const sec of pick) {
    items.push({ id: `${sec.id}_h`, sec, n: 0, text: sec.speech_title, show: sec.title, heading: true });
    sec.sentences.forEach((s, k) => { if (s.s) items.push({ id: `${sec.id}_${k + 1}`, sec, n: k + 1, text: s.s, show: s.t }); });
  }
  return items;
}

function sentenceItem(sec, k) {
  const s = sec.sentences[k];
  return { id: `${sec.id}_${k + 1}`, sec, n: k + 1, text: s.s, show: s.t };
}

function play(items, start = 0) {
  stop();
  if (!items.length) return;
  Object.assign(S, { items, pos: start, playing: true, paused: false });
  buildTimeline();
  $("player").hidden = false;
  speakCurrent();
}

// ---- プログレスバー（今の再生範囲の全体。動かすとその位置へ）----
// 各文の長さ（Mac から来た durations）があれば秒で、無ければ何文目かで表す
const fmt = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(x).padStart(2, "0")}`; };
function buildTimeline() {
  const d = S.paper?.durations;
  S.hasDur = !!d && S.items.every((it) => d[it.id] != null);
  S.offsets = [];
  let t = 0;
  for (const it of S.items) { S.offsets.push(t); t += S.hasDur ? d[it.id] : 1; }
  S.total = t;
}
function currentPos() {
  if (!S.items.length) return 0;
  return S.hasDur ? S.offsets[S.pos] + Math.min(S.player.currentTime || 0, S.paper.durations[S.items[S.pos].id]) : S.pos;
}
function label(v) { return S.hasDur ? fmt(v) : `${Math.min(S.items.length, Math.floor(v) + 1)}文`; }
function tick() {
  if (!S.playing || !S.items.length || S.dragging) return;
  const v = currentPos();
  $("progress").value = S.total ? Math.round((v / S.total) * 1000) : 0;
  $("tCur").textContent = label(v);
  $("tTot").textContent = S.hasDur ? fmt(S.total) : `${S.items.length}文`;
}
function seekTo(frac) {
  if (!S.playing) return;
  const t = Math.max(0, Math.min(S.total - 0.01, frac * S.total));
  let i = S.offsets.length - 1;
  while (i > 0 && S.offsets[i] > t) i--;
  const at = S.hasDur ? t - S.offsets[i] : 0;
  if (i === S.pos && S.hasDur && S.player.src) { S.player.currentTime = at; return; }
  S.pos = i;
  S.paused = false;
  speakCurrent({ at });
}
setInterval(tick, 250);
$("progress").addEventListener("input", () => {
  S.dragging = true;
  $("tCur").textContent = label((Number($("progress").value) / 1000) * S.total);
});
$("progress").addEventListener("change", () => { S.dragging = false; seekTo(Number($("progress").value) / 1000); });

function halt() {
  S.player.pause();
  S.player.removeAttribute("src");
  if (S.url) { URL.revokeObjectURL(S.url); S.url = null; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

function advance(g) { if (g !== S.gen || S.paused) return; S.pos++; speakCurrent(); }

// at: その文の何秒目から、fromEnd: 文の終わりの何秒前から（5秒・10秒戻す・進めるで文をまたぐとき）
async function speakCurrent(opts = {}) {
  const g = ++S.gen;
  halt();
  if (S.pos >= S.items.length) { S.playing = false; $("status").textContent = "読み終わりました"; setMedia(); return; }
  const it = S.items[S.pos];
  showProgress();
  const blob = await db.get("audio", `${S.paper.id}/${it.id}`);
  if (g !== S.gen) return;
  if (blob) {
    S.url = URL.createObjectURL(blob);
    const p = S.player;
    p.src = S.url;
    p.playbackRate = parseFloat($("speed").value);
    p.onended = () => advance(g);
    p.onerror = () => advance(g);
    p.onloadedmetadata = () => {
      if (opts.at) p.currentTime = Math.min(opts.at, p.duration);
      else if (opts.fromEnd) p.currentTime = Math.max(0, p.duration - opts.fromEnd);
    };
    p.play().catch((e) => { if (g === S.gen) $("status").textContent = "再生できませんでした: " + e.message; });
  } else if ("speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(it.text);
    u.lang = "en-US";
    u.rate = parseFloat($("speed").value);
    u.onend = () => advance(g);
    u.onerror = (e) => { if (e.error !== "interrupted" && e.error !== "canceled") advance(g); };
    speechSynthesis.speak(u);
  }
  setMedia();
}

function pauseResume() {
  if (!S.playing) return;
  if (S.paused) { S.paused = false; if (S.player.src) S.player.play(); else speakCurrent(); }
  else { S.paused = true; if (S.player.src) S.player.pause(); else { S.gen++; speechSynthesis.cancel(); } }
  showProgress();
  setMedia();
}
// 5秒・10秒戻す・進める。文の頭・終わりを越えたら前後の文へ続ける（音声ファイルで再生しているときだけ）
function seekBy(sec) {
  const p = S.player;
  if (!S.playing || !p.src) return;
  const t = p.currentTime + sec;
  if (t < 0) {
    if (S.pos === 0) { p.currentTime = 0; return; }
    S.pos--; S.paused = false;
    speakCurrent({ fromEnd: -t });
  } else if (isFinite(p.duration) && t >= p.duration) {
    if (S.pos >= S.items.length - 1) { p.currentTime = Math.max(0, p.duration - 0.05); return; }
    S.pos++; S.paused = false;
    speakCurrent({ at: t - p.duration });
  } else {
    p.currentTime = t;
  }
}

function step(d) { if (!S.playing) return; S.pos = Math.max(0, Math.min(S.items.length - 1, S.pos + d)); S.paused = false; speakCurrent(); }
function stop() {
  S.gen++;
  Object.assign(S, { playing: false, paused: false, items: [] });
  halt();
  $("player").hidden = true;
  setMedia();
}
function showProgress() {
  const it = S.items[S.pos];
  const where = it.heading ? "見出し" : `${it.n}/${it.sec.sentences.length}文`;
  $("status").textContent = `${S.paused ? "一時停止中 — " : ""}${it.sec.title} ・ ${where}（${S.pos + 1}/${S.items.length}）`;
  $("pause").textContent = S.paused ? "▶" : "⏸";
  $("nowText").dataset.sec = it.sec.id;
  fillWords($("nowText"), it.show);
}
// ロック画面・通知の操作
function setMedia() {
  if (!("mediaSession" in navigator)) return;
  const it = S.playing && S.items[S.pos];
  navigator.mediaSession.metadata = it ? new MediaMetadata({ title: it.sec.title, artist: S.paper.meta.title, album: "paper_reader",
    artwork: [{ src: "icons/icon-512.png", sizes: "512x512", type: "image/png" }] }) : null;
  navigator.mediaSession.playbackState = !S.playing ? "none" : S.paused ? "paused" : "playing";
}
if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("play", () => S.paused && pauseResume());
  navigator.mediaSession.setActionHandler("pause", () => !S.paused && pauseResume());
  navigator.mediaSession.setActionHandler("nexttrack", () => step(1));
  navigator.mediaSession.setActionHandler("previoustrack", () => step(-1));
  navigator.mediaSession.setActionHandler("seekbackward", (d) => seekBy(-(d.seekOffset || 10)));   // ロック画面の戻す・進める
  navigator.mediaSession.setActionHandler("seekforward", (d) => seekBy(d.seekOffset || 10));
}
$("playAll").onclick = () => S.paper && play(itemsFor(null));
$("pause").onclick = pauseResume;
$("prev").onclick = () => step(-1);
document.querySelectorAll("[data-seek]").forEach((b) => (b.onclick = () => seekBy(Number(b.dataset.seek))));
$("next").onclick = () => step(1);
$("stop").onclick = stop;
$("speed").oninput = () => {
  $("speedVal").textContent = `${Number($("speed").value).toFixed(2)}×`;
  S.player.playbackRate = parseFloat($("speed").value);
  kv("speed", $("speed").value);
};

// ---- 単語 ----
function audioRef(sentence) {
  if (!S.paper || !sentence) return null;
  for (const sec of S.paper.paper.sections) {
    const k = sec.sentences.findIndex((s) => s.t === sentence);
    if (k >= 0 && sec.sentences[k].s) return `${S.paper.id}/${sec.id}_${k + 1}`;
  }
  return null;
}

async function recordLookup(r, ctx) {
  if (!r.found) return;
  const now = new Date().toISOString();
  const meaning = r.entries.slice(0, 2).map((e) => e.mean).join("\n");
  const ex = ctx.sentence ? [{ sentence: ctx.sentence, section: ctx.section || null, paper_id: S.paper?.id || null,
                                audio_ref: audioRef(ctx.sentence) }] : [];
  const w = (await db.get("words", r.headword)) || { headword: r.headword, meaning, source: r.source, first_seen: now,
                                                      examples: [], origin: "phone" };
  w.last_seen = now;
  w.examples = [...ex, ...(w.examples || [])].slice(0, 3);
  await db.put("words", r.headword, w);
  const lk = { uid: uid(), headword: r.headword, meaning, source: r.source, looked_at: now, query: r.query,
               paper_id: S.paper?.id || null, section: ctx.section || null, sentence: ctx.sentence || null,
               audio_ref: ex[0]?.audio_ref || null };
  await db.put("lookups", lk.uid, lk);
  refreshBadge();
}

function showWord(r, ctx = {}) {
  $("sheet").hidden = false;
  $("wcWord").textContent = r.found ? r.headword : r.normalized || r.query;
  $("wcNote").textContent = r.found && r.note ? `${r.note}（${r.query}）` : "";
  const box = $("wcMeans");
  box.textContent = "";
  if (r.found) {
    for (const e of r.entries) for (const m of e.mean.split(/\n| \/ /)) { const d = document.createElement("div"); d.textContent = m; box.appendChild(d); }
  } else {
    box.textContent = navigator.onLine ? "辞書に見つかりませんでした。" : "辞書に見つかりませんでした（オフライン）。";
  }
  $("wcSource").textContent = r.found ? r.source || "" : "";
  const reg = $("wcRegister");
  reg.hidden = !r.found;
  reg.disabled = !!r.registered;
  reg.textContent = r.registered ? "登録済み ✓" : "単語帳に登録";
  reg.onclick = async () => {                       // 「調べる」→「登録」の2段階。押したときだけ単語帳に入れる
    await recordLookup(r, ctx);
    showWord({ ...r, registered: true }, ctx);
    if (S.view === "words") loadWords();
  };
  $("wcWeblio").href = r.weblio || `https://ejje.weblio.jp/content/${encodeURIComponent(r.headword || r.query)}`;
  $("wcSay").onclick = () => sayWord(r.found ? r.headword : r.normalized);
}

function sayWord(w) {
  if (!("speechSynthesis" in window) || !w) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(w);
  u.lang = "en-US";
  speechSynthesis.speak(u);
}

async function lookupAndShow(word, ctx = {}) {
  const r = await lookup(word);
  if (r.found) r.registered = !!(await db.get("words", r.headword));
  showWord(r, ctx);
}

document.addEventListener("click", (e) => {
  const w = e.target.closest(".w");
  if (!w) return;
  document.querySelectorAll(".w.hit").forEach((x) => x.classList.remove("hit"));
  w.classList.add("hit");
  const secEl = w.closest("[data-sec]");
  const sec = S.paper?.paper.sections.find((s) => s.id === secEl?.dataset.sec);
  lookupAndShow(w.textContent, { section: sec?.title, sentence: w.closest("[data-t]")?.dataset.t || w.closest(".now")?.textContent });
});
$("sheetClose").onclick = () => { $("sheet").hidden = true; };
$("searchForm").onsubmit = (e) => { e.preventDefault(); const w = $("searchInput").value.trim(); if (w) lookupAndShow(w); };

async function loadWords() {
  const t = (w) => new Date(w.last_seen || w.first_seen).getTime();
  const words = (await db.all("words")).sort((a, b) => t(b) - t(a));
  $("wordCount").textContent = words.length ? `${words.length}語` : "";
  $("wordEmpty").hidden = words.length > 0;
  const ul = $("wordList");
  ul.innerHTML = "";
  for (const w of words) {
    const li = document.createElement("li");
    const b = document.createElement("b");
    b.textContent = w.headword;
    const m = document.createElement("span");
    m.className = "m";
    m.textContent = w.meaning.split(/[\n/]/)[0];
    li.append(b, m);
    li.onclick = () => showWord({ found: true, headword: w.headword, query: w.headword, source: w.source,
      entries: w.meaning.split("\n").map((mean) => ({ word: w.headword, mean })), registered: true });
    ul.appendChild(li);
  }
}

// ---- 復習 ----
async function refreshBadge() {
  const q = srs.queue(await db.all("words"), await db.all("reviews"));
  const n = q.counts.new + q.counts.learning + q.counts.review;
  $("dueBadge").hidden = !n;
  $("dueBadge").textContent = n;
  return q;
}

async function loadReview() {
  const q = await refreshBadge();
  S.review = q;
  const c = q.counts;
  $("revCounts").innerHTML = `新 <b class="n">${c.new}</b> ・ 途中 <b class="l">${c.learning}</b> ・ 復習 <b class="v">${c.review}</b>`;
  const card = q.card;
  $("revCard").hidden = !card;
  $("revActions").hidden = !card;
  $("revDone").hidden = !!card;
  if (!card) {
    $("revDone").textContent = c.total === 0 ? "単語帳が空です。論文の文の中の単語をタップして「単語帳に登録」を押すか、Mac のデータを読み込んでください。"
      : `今の分は終わりです。${q.next_due ? `次は ${q.next_due.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} に出ます。` : ""}`;
    return;
  }
  $("revWord").textContent = card.headword;
  const ex = $("revExamples");
  ex.textContent = "";
  for (const e of card.examples || []) {
    if (!e.sentence) continue;
    const d = document.createElement("div");
    d.appendChild(markWord(e.sentence, card.headword));
    if (e.audio_ref) {
      const b = document.createElement("button");
      b.className = "icon";
      b.textContent = "🔈";
      b.onclick = async () => {
        const blob = await db.get("audio", e.audio_ref);
        if (blob) { S.wordAudio.src = URL.createObjectURL(blob); S.wordAudio.play().catch(() => {}); } else sayWord(e.sentence);
      };
      d.append(" ", b);
    }
    if (e.section) { const s = document.createElement("span"); s.className = "src"; s.textContent = e.section; d.appendChild(s); }
    ex.appendChild(d);
  }
  $("revMeaning").textContent = "";
  for (const line of card.meaning.split(/\n| \/ /)) { const d = document.createElement("div"); d.textContent = line; $("revMeaning").appendChild(d); }
  $("revSource").textContent = `${card.source || ""} ・ これまで ${card.reviews} 回`;
  document.querySelectorAll("#revButtons button").forEach((b) => { b.querySelector("small").textContent = card.intervals[b.dataset.r]; });
  $("revBack").hidden = true;
  $("revButtons").hidden = true;
  $("revShow").hidden = false;
}
$("revShow").onclick = () => { $("revBack").hidden = false; $("revButtons").hidden = false; $("revShow").hidden = true; };
document.querySelectorAll("#revButtons button").forEach((b) => (b.onclick = async () => {
  const card = S.review?.card;
  if (!card) return;
  const rv = { uid: uid(), headword: card.headword, rating: Number(b.dataset.r), reviewed_at: new Date().toISOString(), device: S.device };
  await db.put("reviews", rv.uid, rv);
  loadReview();
}));
$("revUndo").onclick = async () => {
  const mine = (await db.all("reviews")).filter((r) => r.device === S.device).sort((a, b) => (a.reviewed_at < b.reviewed_at ? 1 : -1));
  if (!mine.length) { $("revDone").hidden = false; $("revDone").textContent = "取り消せる答えがありません。"; return; }
  await db.del("reviews", mine[0].uid);
  loadReview();
};
$("revSay").onclick = () => S.review?.card && sayWord(S.review.card.headword);

// ---- データの受け渡し ----
async function importBundle(file) {
  $("importMsg").textContent = "読み込んでいます…";
  const files = unzipStored(await file.arrayBuffer());
  const man = JSON.parse(text(files.get("manifest.json") || new Uint8Array()) || "{}");
  if (man.kind !== "paper_reader_bundle") throw new Error("paper_reader の zip ではありません");
  if (man.version !== 1) throw new Error(`zip の版が違います（${man.version}）`);
  let nAudio = 0;
  for (const pid of man.papers) {
    const meta = JSON.parse(text(files.get(`papers/${pid}/meta.json`)));
    const paper = JSON.parse(text(files.get(`papers/${pid}/sentences.json`)));
    const tr = files.get(`papers/${pid}/translation.json`);
    const ja = tr ? JSON.parse(text(tr)).items || {} : {};          // 文ごとの日本語訳（Mac で作ったもの）
    const du = files.get(`papers/${pid}/durations.json`);
    const durations = du ? JSON.parse(text(du)) : null;              // 各文の音声の長さ（プログレスバー用）
    const fj = files.get(`papers/${pid}/figures.json`);
    const figs = fj ? (JSON.parse(text(fj)).items || []) : [];             // 図・表・数式の一覧
    const entries = [["papers", pid, { id: pid, meta, paper, ja, durations, figures: figs }]];
    for (const it of figs) {
      const bytes = files.get(`papers/${pid}/figures/${it.file}`);
      if (bytes) entries.push(["figures", `${pid}/${it.file}`, new Blob([bytes], { type: "image/png" })]);
    }
    for (const [name, bytes] of files) {
      const m = name.match(/^papers\/([0-9a-f]{8})\/audio\/([A-Za-z0-9_]+)\.m4a$/);
      if (m && m[1] === pid) { entries.push(["audio", `${pid}/${m[2]}`, new Blob([bytes], { type: "audio/mp4" })]); nAudio++; }
    }
    await db.putMany(entries);
  }
  const vocab = JSON.parse(text(files.get("vocab.json")));
  const entries = [];
  for (const w of vocab.words) {
    const old = await db.get("words", w.headword);
    entries.push(["words", w.headword, { ...old, ...w, origin: old?.origin === "phone" ? "both" : "mac",
                                         last_seen: old?.last_seen || w.first_seen }]);
  }
  for (const r of vocab.reviews) entries.push(["reviews", r.uid, r]);
  await db.putMany(entries);
  await kv("last_import", { at: new Date().toISOString(), created_at: man.created_at, papers: man.papers.length });
  return `読み込みました: 論文 ${man.papers.length} 本（音声 ${nAudio} 本）、単語 ${vocab.words.length} 語、答えの記録 ${vocab.reviews.length} 件`;
}

$("importInput").onchange = async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  try {
    $("importMsg").textContent = await importBundle(f);
    await loadPapers();
    refreshBadge();
    loadStats();
  } catch (err) { $("importMsg").textContent = "読み込めませんでした: " + err.message; }
};

async function exportProgress() {
  const lookups = await db.all("lookups");
  const reviews = (await db.all("reviews")).filter((r) => r.device === S.device);
  const data = { kind: "paper_reader_progress", version: 1, device: S.device, exported_at: new Date().toISOString(), lookups, reviews };
  const d = new Date(), z = (n) => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}_${z(d.getHours())}${z(d.getMinutes())}`;
  const name = `paper_reader_progress_${stamp}.json`;
  const file = new File([JSON.stringify(data)], name, { type: "application/json" });
  await kv("last_export", { at: data.exported_at, lookups: lookups.length, reviews: reviews.length });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: "paper_reader の記録" }); return `共有しました（${name}）`; }
    catch (e) { if (e.name === "AbortError") return "共有をやめました"; }
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(file);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  return `「ダウンロード」に保存しました（${name}、引いた語 ${lookups.length}・答え ${reviews.length}）`;
}
$("exportBtn").onclick = async () => { $("exportMsg").textContent = await exportProgress(); loadStats(); };

async function loadStats() {
  const [papers, audioKeys, figKeys, words, reviews, lookups] = await Promise.all(
    [db.keys("papers"), db.keys("audio"), db.keys("figures"), db.keys("words"), db.all("reviews"), db.keys("lookups")]);
  const li = [`論文 ${papers.length} 本（音声 ${audioKeys.length} 本・図 ${figKeys.length} 枚）`, `単語帳 ${words.length} 語`,
              `答えの記録 ${reviews.length} 件（このスマホで ${reviews.filter((r) => r.device === S.device).length} 件）`,
              `このスマホで引いた記録 ${lookups.length} 件`];
  const imp = await kv("last_import"), exp = await kv("last_export");
  if (imp) li.push(`最後に読み込んだ: ${new Date(imp.at).toLocaleString("ja-JP")}`);
  if (exp) li.push(`最後に書き出した: ${new Date(exp.at).toLocaleString("ja-JP")}`);
  if (navigator.storage?.estimate) {
    const est = await navigator.storage.estimate();
    li.push(`使っている容量: ${(est.usage / 1e6).toFixed(1)} MB`);
  }
  $("stats").innerHTML = "";
  for (const t of li) { const x = document.createElement("li"); x.textContent = t; $("stats").appendChild(x); }
}

$("dictBtn").onclick = async () => {
  $("dictBtn").disabled = true;
  let ok = 0;
  for (const ch of "abcdefghijklmnopqrstuvwxyz") {
    $("dictBtn").textContent = `保存中… ${ch}`;
    try { if ((await fetch(`dict/${ch}.json`)).ok) ok++; } catch {}
  }
  $("dictBtn").textContent = ok === 26 ? "辞書を保存しました（オフラインで引けます）" : `保存できたのは ${ok}/26（電波のある所でもう一度）`;
  $("dictBtn").disabled = false;
};

$("wipeBtn").onclick = async () => {
  if (!confirm("このスマホの論文・音声・単語帳・復習の記録をすべて消します。Mac に戻していない記録も消えます。よろしいですか？")) return;
  stop();
  await db.clearAll();
  await kv("device", S.device);
  S.paper = null;
  $("paperPick").hidden = false;
  $("paperView").hidden = true;
  await loadPapers();
  refreshBadge();
  loadStats();
};

// ---- 起動 ----
async function init() {
  S.device = (await kv("device")) || `phone-${uid().slice(0, 8)}`;
  await kv("device", S.device);
  const showJa = (await kv("show_ja")) ?? true;
  $("showJa").checked = showJa;
  $("sections").classList.toggle("hide-ja", !showJa);
  $("showJa").onchange = () => { kv("show_ja", $("showJa").checked); $("sections").classList.toggle("hide-ja", !$("showJa").checked); };
  const sp = await kv("speed");
  if (sp) { $("speed").value = sp; $("speedVal").textContent = `${Number(sp).toFixed(2)}×`; }
  $("version").textContent = `版 ${VERSION} ・ この機器の名前 ${S.device}`;
  await loadPapers();
  const last = await kv("last_paper");
  if (last && S.papers.some((p) => p.id === last)) openPaper(last);
  refreshBadge();
  route();
  if ("serviceWorker" in navigator) {
    // 新しい版の Service Worker に切り替わったら1回だけ読み直す（新しい画面と古いプログラムが混ざらないように）
    const had = !!navigator.serviceWorker.controller;
    let reloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (had && !reloaded) { reloaded = true; location.reload(); }
    });
    navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).catch(() => {});
  }
  if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});   // 容量が足りなくなっても消されにくくする
}
init();
