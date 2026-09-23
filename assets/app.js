// paper_reader の画面。論文の一覧・取り込み・章ごとの読み上げ・単語検索と単語帳。
// 読み上げは、サーバーが say で作った1文ずつの m4a を順に鳴らす（どのブラウザでも同じ声）。
// 音声がまだできていない間だけ、ブラウザの読み上げ（Web Speech API）で代わりに読む。
"use strict";
const $ = (id) => document.getElementById(id);
const S = { papers: [], paper: null, audio: null, items: [], pos: 0, playing: false, paused: false, gen: 0,
            cfg: null, player: new Audio(), poll: null, wordAudio: new Audio() };

const pref = {
  get(k, d) { try { const v = localStorage.getItem("paper_reader." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("paper_reader." + k, JSON.stringify(v)); } catch {} },
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify(body || {}) });

// ---- 文を、クリックできる単語に分けて入れる ----
function fillWords(el, text) {
  el.textContent = "";
  for (const part of text.split(/([A-Za-z](?:[A-Za-z'’\-]*[A-Za-z])?)/)) {
    if (!part) continue;
    if (/^[A-Za-z]/.test(part)) {
      const s = document.createElement("span");
      s.className = "w";
      s.textContent = part;
      el.appendChild(s);
    } else {
      el.appendChild(document.createTextNode(part));
    }
  }
}

// ---- 論文の一覧と取り込み ----
async function loadPapers(selectId) {
  S.papers = await api("/api/papers");
  const ul = $("paperList");
  ul.innerHTML = "";
  $("paperEmpty").hidden = S.papers.length > 0;
  for (const m of S.papers) {
    const li = document.createElement("li");
    li.dataset.id = m.id;
    li.textContent = m.title;
    const sm = document.createElement("small");
    sm.textContent = `${m.pages}ページ・${m.sections}章・${m.sentences}文` + (m.ocr_pages?.length ? `・OCR ${m.ocr_pages.length}ページ` : "");
    li.appendChild(sm);
    li.onclick = () => openPaper(m.id);
    ul.appendChild(li);
  }
  const id = selectId || pref.get("last", null);
  if (id && S.papers.some((m) => m.id === id)) openPaper(id);
}

async function importFiles(files) {
  const pdfs = [...files].filter((f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name));
  if (!pdfs.length) { $("importMsg").textContent = "PDF が選ばれていません"; return; }
  let last = null;
  for (const f of pdfs) {
    $("importMsg").textContent = `取り込み中: ${f.name}（OCR が要るページがあると時間がかかります）`;
    try {
      const m = await api("/api/import", { method: "POST", body: f,
        headers: { "Content-Type": "application/pdf", "X-Filename": encodeURIComponent(f.name) } });
      last = m.id;
      $("importMsg").textContent = `${m.already ? "既にあります" : "取り込みました（音声を作っています）"}: ${m.title}`;
    } catch (e) {
      $("importMsg").textContent = `${f.name}: ${e.message}`;
    }
  }
  await loadPapers(last);
}

// ---- 論文を開く ----
async function openPaper(id) {
  stop();
  const p = await api(`/api/papers/${id}`);
  S.paper = p;
  S.audio = p.audio;
  pref.set("last", id);
  document.querySelectorAll("#paperList li").forEach((li) => li.classList.toggle("on", li.dataset.id === id));
  $("placeholder").hidden = true;
  $("paper").hidden = false;
  const meta = S.papers.find((m) => m.id === id) || {};
  $("paperTitle").textContent = p.title || meta.title || id;
  $("paperMeta").textContent = `${meta.source_name || ""} ・ ${p.pages}ページ ・ ${p.sections.length}章`
    + (p.ocr_pages.length ? ` ・ OCR したページ: ${p.ocr_pages.join(", ")}` : "");
  renderSections();
  renderAudio();
  watchAudio();
}

function chapterFile(sec) {
  return (S.audio?.chapters || []).find((c) => c.title === sec.title)?.file;
}

function renderSections() {
  const p = S.paper;
  const ol = $("sections");
  ol.innerHTML = "";
  for (const sec of p.sections) {
    const li = document.createElement("li");
    li.className = `sec l${Math.min(sec.level, 3)}` + (sec.kind === "back" ? " back" : "");
    li.dataset.sec = sec.id;
    const head = document.createElement("div");
    head.className = "head";
    const btn = document.createElement("button");
    btn.className = "play";
    btn.textContent = "▶";
    btn.title = sec.level === 1 ? "この章（節も含む）を読む" : "この節を読む";
    btn.onclick = () => play(itemsFor(sec.id));
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = sec.title;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${sec.sentences.length}文 ・ p.${sec.page}`;
    head.append(btn, name, count);
    const f = S.audio?.state === "done" && chapterFile(sec);
    if (f) {
      const a = document.createElement("a");
      a.className = "dl";
      a.href = `/api/papers/${p.id}/export/${f}`;
      a.download = f;
      a.textContent = "m4a";
      a.title = "この章の音声ファイル（スマホに持ち出す用）";
      head.appendChild(a);
    }
    li.appendChild(head);
    if (sec.sentences.length) {
      const det = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = "文を見る（単語をクリックで辞書）";
      const list = document.createElement("ol");
      for (const s of sec.sentences) {
        const x = document.createElement("li");
        fillWords(x, s.t);
        list.appendChild(x);
      }
      det.append(sum, list);
      li.appendChild(det);
    }
    ol.appendChild(li);
  }
}

// ---- 音声の作成状態 ----
function renderAudio() {
  const a = S.audio || { state: "none" };
  const line = $("audioLine");
  line.textContent = "";
  const span = document.createElement("span");
  if (a.state === "done") {
    span.textContent = `音声: ${a.voice}・${a.rate} 語/分・${a.total} 本 ・ スマホ用に章ごと ${a.chapters.length} 本 + 全体 1 本`;
    const b = document.createElement("button");
    b.className = "small";
    b.textContent = "Finder で音声フォルダを開く";
    b.title = "export/ に章ごとの m4a（AAC）。AirDrop などで iPhone・Android へ";
    b.onclick = () => post(`/api/papers/${S.paper.id}/reveal`);
    line.append(span, b);
  } else if (a.state === "running") {
    span.textContent = `音声を作っています ${a.done} / ${a.total}（できるまではブラウザの読み上げで代わりに読みます）`;
    line.append(span);
  } else if (a.state === "error") {
    span.textContent = `音声を作れませんでした: ${a.error}`;
    const b = document.createElement("button");
    b.className = "small";
    b.textContent = "作り直す";
    b.onclick = regenerate;
    line.append(span, b);
  } else {
    span.textContent = "音声はまだありません";
    line.append(span);
  }
  $("regen").hidden = !S.cfg || !a.voice || $("voice").value === a.voice;
}

function watchAudio() {
  clearInterval(S.poll);
  if (S.audio?.state !== "running") return;
  const id = S.paper.id;
  S.poll = setInterval(async () => {
    if (!S.paper || S.paper.id !== id) return clearInterval(S.poll);
    S.audio = await api(`/api/papers/${id}/audio`).catch(() => S.audio);
    renderAudio();
    if (S.audio.state !== "running") { clearInterval(S.poll); renderSections(); }
  }, 2000);
}

async function regenerate() {
  stop();
  await post("/api/config", { voice: $("voice").value });
  const st = await post(`/api/papers/${S.paper.id}/audio`);
  S.audio = st;
  renderAudio();
  watchAudio();
}

// ---- 読む範囲を作る ----
// secId なし = 全体。章（level 1）なら、次の章までの節も含める。
// id はサーバーの音声ファイル名と同じ（<sec>_h, <sec>_<k>）。
function itemsFor(secId) {
  const secs = S.paper.sections;
  let pick;
  if (!secId) {
    pick = secs.filter((s) => !($("skipBack").checked && s.kind === "back"));
  } else {
    const i = secs.findIndex((s) => s.id === secId);
    let j = i + 1;
    while (j < secs.length && secs[j].level > secs[i].level) j++;
    pick = secs.slice(i, j);
  }
  const items = [];
  for (const sec of pick) {
    items.push({ id: `${sec.id}_h`, sec, n: 0, text: sec.speech_title, show: sec.title, heading: true });
    sec.sentences.forEach((s, k) => {
      if (s.s) items.push({ id: `${sec.id}_${k + 1}`, sec, n: k + 1, text: s.s, show: s.t });
    });
  }
  return items;
}

// ---- 読み上げ ----
function play(items, start = 0) {
  stop();
  if (!items.length) return;
  S.items = items;
  S.pos = start;
  S.playing = true;
  S.paused = false;
  speakCurrent();
}

function haltOutput() {
  S.player.pause();
  S.player.removeAttribute("src");
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

function advance(g) {
  if (g !== S.gen || S.paused) return;
  S.pos++;
  speakCurrent();
}

function speakCurrent() {
  const g = ++S.gen;
  haltOutput();
  if (S.pos >= S.items.length) { S.playing = false; status("読み終わりました"); $("nowText").hidden = true; return; }
  const it = S.items[S.pos];
  showProgress();
  if (S.audio?.state === "done") {
    const p = S.player;
    p.src = `/api/papers/${S.paper.id}/audio/${it.id}.m4a`;
    p.playbackRate = parseFloat($("speed").value);
    p.onended = () => advance(g);
    p.onerror = () => advance(g);
    p.play().catch((e) => { if (g === S.gen) status("再生できませんでした: " + e.message); });
  } else if ("speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(it.text);
    u.lang = "en-US";
    u.rate = parseFloat($("speed").value);
    u.onend = () => advance(g);
    u.onerror = (e) => { if (e.error !== "interrupted" && e.error !== "canceled") advance(g); };
    speechSynthesis.speak(u);
  } else {
    status("音声ができるまでお待ちください");
  }
}

function pauseResume() {
  if (!S.playing) return;
  if (S.paused) {
    S.paused = false;
    if (S.audio?.state === "done" && S.player.src) { S.player.play(); showProgress(); }
    else speakCurrent();                          // ブラウザ読み上げはその文の頭から
  } else {
    S.paused = true;
    if (S.audio?.state === "done") S.player.pause();
    else { S.gen++; speechSynthesis.cancel(); }
    showProgress();
  }
}

function step(d) {
  if (!S.playing) return;
  S.pos = Math.max(0, Math.min(S.items.length - 1, S.pos + d));
  S.paused = false;
  speakCurrent();
}

function stop() {
  S.gen++;
  S.playing = false;
  S.paused = false;
  S.items = [];
  haltOutput();
  status("停止中");
  $("pause").textContent = "⏸";
  $("nowText").hidden = true;
}

function showProgress() {
  const it = S.items[S.pos];
  const where = it.heading ? "見出し" : `${it.n} / ${it.sec.sentences.length} 文`;
  status(`${S.paused ? "一時停止中 — " : ""}${it.sec.title} ・ ${where}（全体 ${S.pos + 1} / ${S.items.length}）`);
  $("pause").textContent = S.paused ? "▶" : "⏸";
  const now = $("nowText");
  now.hidden = false;
  now.dataset.sec = it.sec.id;
  fillWords(now, it.show);
}

function status(t) { $("status").textContent = t; }

// ---- 単語検索と単語帳 ----
async function lookup(word, ctx = {}) {
  const qs = new URLSearchParams({ w: word });
  if (S.paper) qs.set("paper", S.paper.id);
  if (ctx.sec) qs.set("sec", ctx.sec);
  if (ctx.sentence) qs.set("sentence", ctx.sentence);
  const r = await api(`/api/lookup?${qs}`);
  showWord(r);
  if (r.vocab_id) loadVocab();
}

function showWord(r) {
  $("wordCard").hidden = false;
  $("wcWord").textContent = r.found ? r.headword : r.normalized || r.query;
  $("wcNote").textContent = r.found && r.note ? `${r.note}（${r.query}）` : "";
  const box = $("wcMeans");
  box.textContent = "";
  if (r.found) {
    for (const e of r.entries) {
      if (r.entries.length > 1) {
        const h = document.createElement("div");
        h.innerHTML = "<b></b>";
        h.firstChild.textContent = e.word;
        box.appendChild(h);
      }
      for (const m of e.mean.split(" / ")) {
        const d = document.createElement("div");
        d.textContent = m;
        box.appendChild(d);
      }
    }
  } else {
    box.textContent = "辞書に見つかりませんでした。右下の Weblio で見てください（単語帳には入れていません）。";
  }
  $("wcSource").textContent = r.found ? `${r.source}${r.vocab_id ? " ・ 単語帳に保存" : ""}` : "";
  $("wcWeblio").href = r.weblio;
  $("wcSay").onclick = () => {
    S.wordAudio.src = `/api/word_audio?w=${encodeURIComponent(r.found ? r.headword : r.normalized)}`;
    S.wordAudio.play().catch(() => {});
  };
}

async function loadVocab() {
  const vs = await api("/api/vocab");
  const ul = $("vocabList");
  ul.innerHTML = "";
  $("vocabEmpty").hidden = vs.length > 0;
  $("vocabCount").textContent = vs.length ? `${vs.length}語` : "";
  for (const v of vs) {
    const li = document.createElement("li");
    const b = document.createElement("b");
    b.textContent = v.headword;
    const m = document.createElement("span");
    m.className = "m";
    m.textContent = v.meaning.split(/[\n/]/)[0];
    const del = document.createElement("button");
    del.className = "del";
    del.textContent = "×";
    del.title = "単語帳から消す";
    del.onclick = async (e) => { e.stopPropagation(); await post(`/api/vocab/${v.id}/delete`); loadVocab(); };
    li.title = v.example ? `例: ${v.example}` : "";
    li.append(b, m, del);
    li.onclick = () => showWord({ found: true, headword: v.headword, query: v.headword, source: v.source,
      entries: v.meaning.split("\n").map((mean) => ({ word: v.headword, mean })), vocab_id: v.id,
      weblio: `https://ejje.weblio.jp/content/${encodeURIComponent(v.headword)}` });
    ul.appendChild(li);
  }
}

document.addEventListener("click", (e) => {
  const w = e.target.closest(".w");
  if (!w) return;
  document.querySelectorAll(".w.hit").forEach((x) => x.classList.remove("hit"));
  w.classList.add("hit");
  const secEl = w.closest("[data-sec]");
  const sec = S.paper?.sections.find((s) => s.id === secEl?.dataset.sec);
  const sentence = w.closest("li, .now")?.textContent;
  lookup(w.textContent, { sec: sec?.title, sentence });
});

// ---- 設定 ----
async function loadConfig() {
  S.cfg = await api("/api/config");
  const sel = $("voice");
  sel.innerHTML = "";
  for (const v of S.cfg.voices) {
    const o = document.createElement("option");
    o.value = v.name;
    o.textContent = `${v.name} (${v.lang})`;
    sel.appendChild(o);
  }
  sel.value = S.cfg.voice;
  if (!S.cfg.dict) $("searchInput").placeholder = "辞書が未作成（README 参照）";
}

// ---- つなぎこみ ----
$("playAll").onclick = () => S.paper && play(itemsFor(null));
$("pause").onclick = pauseResume;
$("stop").onclick = stop;
$("prev").onclick = () => step(-1);
$("next").onclick = () => step(1);
$("speed").value = pref.get("speed", 1);
$("speedVal").textContent = Number($("speed").value).toFixed(2);
$("speed").oninput = () => {
  $("speedVal").textContent = Number($("speed").value).toFixed(2);
  S.player.playbackRate = parseFloat($("speed").value);
  pref.set("speed", $("speed").value);
};
$("voice").onchange = renderAudio;
$("regen").onclick = regenerate;
$("skipBack").checked = pref.get("skipBack", true);
$("skipBack").onchange = () => pref.set("skipBack", $("skipBack").checked);
$("importInput").onchange = (e) => { importFiles(e.target.files); e.target.value = ""; };
$("searchForm").onsubmit = (e) => {
  e.preventDefault();
  const w = $("searchInput").value.trim();
  if (w) lookup(w);
};
$("wordClose").onclick = () => { $("wordCard").hidden = true; };
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !/INPUT|SELECT|TEXTAREA|BUTTON/.test(e.target.tagName)) { e.preventDefault(); pauseResume(); }
  if (e.key === "Escape") $("wordCard").hidden = true;
});
let dragDepth = 0;
addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("drop").hidden = false; });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("drop").hidden = true; } });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("drop").hidden = true; importFiles(e.dataTransfer.files); });

loadConfig().catch(() => {});
loadVocab().catch(() => {});
loadPapers().catch((e) => status("サーバーにつながりません: " + e.message));
