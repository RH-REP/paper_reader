// paper_reader の画面。論文の一覧・取り込み・章ごとの読み上げ（Web Speech API = Mac の読み上げ音声）。
// 読み上げは1文 = 1発話で順に流す。一時停止は「今の文を止めて、再開でその文の頭から」。
"use strict";
const $ = (id) => document.getElementById(id);
const S = { papers: [], paper: null, items: [], pos: 0, playing: false, paused: false, gen: 0, voices: [] };

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
      $("importMsg").textContent = `${m.already ? "既にあります" : "取り込みました"}: ${m.title}`;
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
  pref.set("last", id);
  document.querySelectorAll("#paperList li").forEach((li) => li.classList.toggle("on", li.dataset.id === id));
  $("placeholder").hidden = true;
  $("paper").hidden = false;
  const meta = S.papers.find((m) => m.id === id) || {};
  $("paperTitle").textContent = p.title || meta.title || id;
  $("paperMeta").textContent = `${meta.source_name || ""} ・ ${p.pages}ページ ・ ${p.sections.length}章`
    + (p.ocr_pages.length ? ` ・ OCR したページ: ${p.ocr_pages.join(", ")}` : "");
  const ol = $("sections");
  ol.innerHTML = "";
  for (const sec of p.sections) {
    const li = document.createElement("li");
    li.className = `sec l${Math.min(sec.level, 3)}` + (sec.kind === "back" ? " back" : "");
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
    li.appendChild(head);
    if (sec.sentences.length) {
      const det = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = "文を見る";
      const list = document.createElement("ol");
      for (const s of sec.sentences) {
        const x = document.createElement("li");
        x.textContent = s.t;
        list.appendChild(x);
      }
      det.append(sum, list);
      li.appendChild(det);
    }
    ol.appendChild(li);
  }
}

// ---- 読む範囲を作る ----
// secId なし = 全体。章（level 1）なら、次の章までの節も含める。
function itemsFor(secId) {
  const secs = S.paper.sections;
  let pick;
  if (!secId) {
    pick = secs.filter((s) => !($("skipBack").checked && s.kind === "back"));
  } else {
    const i = secs.findIndex((s) => s.id === secId);
    const lv = secs[i].level;
    let j = i + 1;
    while (j < secs.length && secs[j].level > lv) j++;
    pick = secs.slice(i, j);
  }
  const cites = $("skipCites").checked;
  const items = [];
  for (const sec of pick) {
    items.push({ sec, n: 0, text: sec.speech_title, heading: true });
    sec.sentences.forEach((s, k) => {
      const text = cites ? s.s : s.t;
      if (text) items.push({ sec, n: k + 1, text });
    });
  }
  return items;
}

// ---- 読み上げ ----
function englishVoices() {
  const vs = speechSynthesis.getVoices().filter((v) => /^en[-_]/i.test(v.lang));
  const score = (v) => (/premium/i.test(v.name) ? 0 : /enhanced/i.test(v.name) ? 1 : /^(samantha|daniel|karen|moira|alex)/i.test(v.name) ? 2 : 3);
  return vs.sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name));
}

function fillVoices() {
  S.voices = englishVoices();
  const sel = $("voice");
  const want = pref.get("voice", null);
  sel.innerHTML = "";
  for (const v of S.voices) {
    const o = document.createElement("option");
    o.value = v.name;
    o.textContent = `${v.name} (${v.lang})`;
    sel.appendChild(o);
  }
  if (want && S.voices.some((v) => v.name === want)) sel.value = want;
  if (!S.voices.length) {
    const o = document.createElement("option");
    o.textContent = "英語の声が見つかりません";
    sel.appendChild(o);
  }
}

function play(items, start = 0) {
  stop();
  if (!items.length) return;
  S.items = items;
  S.pos = start;
  S.playing = true;
  S.paused = false;
  speakCurrent();
}

function speakCurrent() {
  const g = ++S.gen;
  speechSynthesis.cancel();
  if (S.pos >= S.items.length) { S.playing = false; status("読み終わりました"); return; }
  const it = S.items[S.pos];
  const u = new SpeechSynthesisUtterance(it.text);
  const v = S.voices.find((x) => x.name === $("voice").value);
  if (v) u.voice = v;
  u.lang = v ? v.lang : "en-US";
  u.rate = parseFloat($("rate").value);
  u.onend = () => { if (g !== S.gen || S.paused) return; S.pos++; speakCurrent(); };
  u.onerror = (e) => {
    if (g !== S.gen || e.error === "interrupted" || e.error === "canceled") return;
    S.pos++; speakCurrent();
  };
  showProgress();
  speechSynthesis.speak(u);
}

function pauseResume() {
  if (!S.playing) return;
  if (S.paused) { S.paused = false; speakCurrent(); }
  else { S.paused = true; S.gen++; speechSynthesis.cancel(); showProgress(); }
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
  speechSynthesis.cancel();
  status("停止中");
  $("pause").textContent = "⏸";
}

function showProgress() {
  const it = S.items[S.pos];
  const total = it.sec.sentences.length;
  const where = it.heading ? "見出し" : `${it.n} / ${total} 文`;
  status(`${S.paused ? "一時停止中 — " : ""}${it.sec.title} ・ ${where}（全体 ${S.pos + 1} / ${S.items.length}）`);
  $("pause").textContent = S.paused ? "▶" : "⏸";
}

function status(t) { $("status").textContent = t; }

// ---- つなぎこみ ----
$("playAll").onclick = () => S.paper && play(itemsFor(null));
$("pause").onclick = pauseResume;
$("stop").onclick = stop;
$("prev").onclick = () => step(-1);
$("next").onclick = () => step(1);
$("rate").value = pref.get("rate", 1);
$("rateVal").textContent = Number($("rate").value).toFixed(2);
$("rate").oninput = () => { $("rateVal").textContent = Number($("rate").value).toFixed(2); pref.set("rate", $("rate").value); };
$("voice").onchange = () => pref.set("voice", $("voice").value);
$("skipCites").checked = pref.get("skipCites", true);
$("skipBack").checked = pref.get("skipBack", true);
$("skipCites").onchange = () => pref.set("skipCites", $("skipCites").checked);
$("skipBack").onchange = () => pref.set("skipBack", $("skipBack").checked);
$("importInput").onchange = (e) => { importFiles(e.target.files); e.target.value = ""; };
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !/INPUT|SELECT|TEXTAREA|BUTTON/.test(e.target.tagName)) { e.preventDefault(); pauseResume(); }
});
let dragDepth = 0;
addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("drop").hidden = false; });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("drop").hidden = true; } });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("drop").hidden = true; importFiles(e.dataTransfer.files); });

fillVoices();
speechSynthesis.onvoiceschanged = fillVoices;
loadPapers().catch((e) => status("サーバーにつながりません: " + e.message));
