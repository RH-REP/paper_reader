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
  for (const [n, f] of pdfs.entries()) {
    const head = pdfs.length > 1 ? `(${n + 1}/${pdfs.length}) ` : "";
    try {
      const m = await uploadWithProgress(f, head);
      last = m.id;
      $("importMsg").textContent = `${head}${m.already ? "既にあります" : "取り込みました（続けて音声と訳を作ります）"}: ${m.title}`;
    } catch (e) {
      $("importMsg").textContent = `${head}${f.name}: ${e.message}`;
    }
  }
  await loadPapers(last);
}

// 取り込みのプログレスバー: ① PDF を送る（送った割合）→ ② 文字を取り出す（何ページ目か。サーバーに聞く）
function uploadWithProgress(file, head) {
  const show = (st, title, opts) => { $("importMsg").textContent = ""; $("importMsg").appendChild(jobBar(st, head + title, opts)); };
  const started = new Date().toISOString();
  show({ done: 0, total: 100, started_at: started }, `送っています ${file.name}`, { pct: true });
  let poll = null;
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api/import");
    x.setRequestHeader("Content-Type", "application/pdf");
    x.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    x.upload.onprogress = (e) => { if (e.lengthComputable) show({ done: Math.round((e.loaded / e.total) * 100), total: 100 }, `送っています ${file.name}`, { pct: true }); };
    x.upload.onload = () => {
      show({ done: 0, total: 0 }, "文字を取り出しています…");
      poll = setInterval(async () => {
        const p = await api("/api/import").catch(() => null);
        if (p?.active && p.pages) show({ done: p.page, total: p.pages, started_at: p.started_at },
          `文字を取り出しています（ページ${p.ocr ? `、うち OCR ${p.ocr}` : ""}）`);
      }, 400);
    };
    x.onload = () => {
      clearInterval(poll);
      let j = {};
      try { j = JSON.parse(x.responseText); } catch {}
      x.status >= 200 && x.status < 300 ? resolve(j) : reject(new Error(j.error || x.statusText));
    };
    x.onerror = () => { clearInterval(poll); reject(new Error("サーバーにつながりません")); };
    x.send(file);
  });
}

// ---- 論文を開く ----
async function openPaper(id) {
  stop();
  let p;
  try { p = await api(`/api/papers/${id}`); }
  catch (e) { status(`論文を開けません: ${e.message}`); $("placeholder").hidden = false; $("placeholder").textContent = `論文を開けません: ${e.message}`; return; }
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
  renderTranslation();
  watchTranslation();
  $("aiPanel").hidden = true;
  loadFigures();
  $("manualTag").textContent = p.manual ? `AI 手直し済み（${(p.manual.at || "").slice(0, 16).replace("T", " ")}）${p.manual.notes ? `: ${p.manual.notes}` : ""}` : "";
}

// ---- 図・表・数式（画像として開く）----
async function loadFigures() {
  const id = S.paper.id;
  $("figPanel").hidden = true;
  $("figBtn").hidden = true;
  const st = await api(`/api/papers/${id}/figures`).catch(() => ({ items: [] }));
  if (!S.paper || S.paper.id !== id) return;
  S.figures = (st.items || []).map((it) => ({ ...it, url: `/api/papers/${id}/figures/${it.file}` }));
  const n = S.figures.length;
  $("figBtn").hidden = !n;
  $("figBtn").textContent = `図・表・数式（${n}）${st.manual ? " ・AI 手直し済み" : ""}`;
  const grid = $("figGrid");
  grid.textContent = "";
  S.figures.forEach((f, i) => {
    const fig = document.createElement("figure");
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = f.url;
    img.alt = f.label || f.file;
    const cap = document.createElement("figcaption");
    cap.textContent = `${f.label || { image: "画像", figure: "図", table: "表", equation: "数式" }[f.kind] || "画像"} ・ p.${f.page}`;
    fig.append(img, cap);
    fig.onclick = () => openLightbox(i);
    grid.appendChild(fig);
  });
}
$("figBtn").onclick = () => { $("figPanel").hidden = !$("figPanel").hidden; };

function openLightbox(i) {
  const n = S.figures.length;
  if (!n) return;
  S.lbIndex = (i + n) % n;
  const f = S.figures[S.lbIndex];
  $("lbImg").src = f.url;
  $("lbImg").alt = f.label || f.file;
  $("lbLabel").textContent = f.label || "画像";
  $("lbCount").textContent = `${S.lbIndex + 1} / ${n} ・ p.${f.page}${f.by === "ai" ? " ・ AI" : ""}`;
  $("lbCaption").textContent = f.caption || "";
  $("lbOpen").href = f.url;
  $("lightbox").hidden = false;
}
function closeLightbox() { $("lightbox").hidden = true; }
$("lbPrev").onclick = () => openLightbox(S.lbIndex - 1);
$("lbNext").onclick = () => openLightbox(S.lbIndex + 1);
$("lbClose").onclick = closeLightbox;
$("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") closeLightbox(); });

// ---- AI に手直しを頼むプロンプト（フォルダの場所 ＋ 決まった依頼文）----
$("aiBtn").onclick = async () => {
  const r = await api(`/api/papers/${S.paper.id}/ai_prompt`);
  $("aiText").value = r.prompt;
  $("aiPanel").hidden = false;
  $("aiMsg").textContent = "";
};
$("aiCopy").onclick = async () => {
  const t = $("aiText");
  try { await navigator.clipboard.writeText(t.value); }
  catch { t.select(); document.execCommand("copy"); }
  $("aiMsg").textContent = "コピーしました。AI に貼り付けてください";
};
$("aiClose").onclick = () => { $("aiPanel").hidden = true; };

// 作っているあいだのプログレスバー: 「音声を作っています ▰▰▱ 37 / 162（23%・残り約1分）」
// opts.pct: 「37 / 162」を出さず割合だけ（送った割合など）。total が 0 なら、まだ量がわからない（棒は左右に動く表示）
function jobBar(st, title, opts = {}) {
  const box = document.createElement("span");
  box.className = "job";
  const bar = document.createElement("progress");
  if (st.total) {
    bar.max = st.total;
    bar.value = st.phase === "chapters" ? bar.max : st.done || 0;
  }
  const pct = st.total ? Math.floor(((st.done || 0) / st.total) * 100) : 0;
  let eta = "";
  if (st.started_at && st.done > 2 && st.done < st.total) {
    const sec = (Date.now() - new Date(st.started_at).getTime()) / 1000 / st.done * (st.total - st.done);
    eta = sec < 60 ? "・残り1分以内" : `・残り約${Math.round(sec / 60)}分`;
  }
  const txt = document.createElement("span");
  txt.textContent = st.phase === "chapters" || !st.total ? title
    : opts.pct ? `${title}（${pct}%）` : `${title} ${st.done || 0} / ${st.total}（${pct}%${eta}）`;
  box.append(txt, bar);
  return box;
}

// ---- 文ごとの日本語訳（macOS 内蔵の翻訳。端末内）----
function renderTranslation() {
  const t = S.paper.translation || { state: "none" };
  const line = $("trLine");
  line.textContent = "";
  const span = document.createElement("span");
  const msg = { done: `訳: ${Object.keys(S.paper.ja || {}).length} 文（Mac 内蔵の翻訳）`,
                running: "",
                need_install: `訳: 英語・日本語の翻訳データが Mac に入っていません。${t.error || ""}`,
                unsupported: "訳: この Mac では英語→日本語の翻訳が使えません",
                error: `訳を作れませんでした: ${t.error || ""}`, none: "訳はまだありません" }[t.state] || "";
  span.textContent = msg;
  line.appendChild(t.state === "running" ? jobBar(t, "訳を作っています") : span);
  if (t.state !== "running") {
    const b = document.createElement("button");
    b.className = "small";
    b.textContent = "訳を作り直す";
    b.onclick = async () => { await post(`/api/papers/${S.paper.id}/translate`); S.paper.translation = { state: "running", done: 0, total: 0 }; renderTranslation(); watchTranslation(); };
    line.appendChild(b);
  }
}

function watchTranslation() {
  clearInterval(S.trPoll);
  if (S.paper?.translation?.state !== "running" && S.paper?.translation?.state !== "none") return;
  let waited = 0;
  const id = S.paper.id;
  S.trPoll = setInterval(async () => {
    if (!S.paper || S.paper.id !== id) return clearInterval(S.trPoll);
    const t = await api(`/api/papers/${id}/translation`).catch(() => null);
    if (!t) return;
    S.paper.ja = t.ja;
    delete t.ja;
    S.paper.translation = t;
    renderTranslation();
    if (t.state === "none" && ++waited < 30) return;
    if (t.state !== "running") { clearInterval(S.trPoll); renderSections(); }
  }, 2000);
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
        const ja = S.paper.ja?.[`${sec.id}_${k + 1}`];
        if (ja) {
          const j = document.createElement("div");
          j.className = "ja";
          j.textContent = ja;
          x.appendChild(j);
        }
        list.appendChild(x);
      });
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
    line.append(jobBar(a, a.phase === "chapters" ? "章ごとのファイルを作っています" : "音声を作っています"),
                Object.assign(document.createElement("span"), { className: "muted", textContent: "（できるまではブラウザの読み上げで代わりに読みます）" }));
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
  // 取り込んだ直後は、開いた時点でまだ音声づくりが始まっていない（none）ことがあるので、始まるまで見張る（最大30秒）
  if (S.audio?.state !== "running" && S.audio?.state !== "none") return;
  const id = S.paper.id;
  let waited = 0;
  S.poll = setInterval(async () => {
    if (!S.paper || S.paper.id !== id) return clearInterval(S.poll);
    S.audio = await api(`/api/papers/${id}/audio`).catch(() => S.audio);
    renderAudio();
    if (S.audio.state === "none" && ++waited < 30) return;
    if (S.audio.state !== "running") { clearInterval(S.poll); renderSections(); }
  }, 1000);
}

async function regenerate() {
  stop();
  await post("/api/config", { voice: $("voice").value });
  const st = await post(`/api/papers/${S.paper.id}/audio`);
  // 押した直後はまだ前の「作り終えた」状態が返ることがあるので、作り始めとして見せて見張りを始める
  S.audio = st.started ? { ...st, state: "running", done: 0, phase: null, started_at: new Date().toISOString() } : st;
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

function sentenceItem(sec, k) {
  const s = sec.sentences[k];
  return { id: `${sec.id}_${k + 1}`, sec, n: k + 1, text: s.s, show: s.t };
}

// ---- 読み上げ ----
function play(items, start = 0) {
  stop();
  if (!items.length) return;
  S.items = items;
  S.pos = start;
  S.playing = true;
  S.paused = false;
  buildTimeline();
  $("progRow").hidden = false;
  speakCurrent();
}

// ---- プログレスバー（今の再生範囲の全体。動かすとその位置へ）----
// 各文の長さ（audio.json の durations）があれば秒で、無ければ（ブラウザ読み上げの間）何文目かで表す
const fmt = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(x).padStart(2, "0")}`; };
function buildTimeline() {
  const d = S.audio?.state === "done" ? S.audio.durations : null;
  S.hasDur = !!d && S.items.every((it) => d[it.id] != null);
  S.offsets = [];
  let t = 0;
  for (const it of S.items) { S.offsets.push(t); t += S.hasDur ? d[it.id] : 1; }
  S.total = t;
}
function currentPos() {
  if (!S.items.length) return 0;
  return S.hasDur ? S.offsets[S.pos] + Math.min(S.player.currentTime || 0, S.audio.durations[S.items[S.pos].id]) : S.pos;
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
$("progress").addEventListener("change", () => {
  S.dragging = false;
  seekTo(Number($("progress").value) / 1000);
  $("progress").blur();
});

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

// at: その文の何秒目から、fromEnd: 文の終わりの何秒前から（5秒・10秒戻す・進めるで文をまたぐとき）
function speakCurrent(opts = {}) {
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
    p.onloadedmetadata = () => {
      if (opts.at) p.currentTime = Math.min(opts.at, p.duration);
      else if (opts.fromEnd) p.currentTime = Math.max(0, p.duration - opts.fromEnd);
    };
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

// 5秒・10秒戻す・進める。文の頭・終わりを越えたら前後の文へ続ける（音声ファイルで再生しているときだけ）
function seekBy(sec) {
  if (!S.playing || S.audio?.state !== "done" || !S.player.src) return;
  const p = S.player;
  const t = p.currentTime + sec;
  if (t < 0) {
    if (S.pos === 0) { p.currentTime = 0; return; }
    S.pos--;
    S.paused = false;
    speakCurrent({ fromEnd: -t });
  } else if (isFinite(p.duration) && t >= p.duration) {
    if (S.pos >= S.items.length - 1) { p.currentTime = Math.max(0, p.duration - 0.05); return; }
    S.pos++;
    S.paused = false;
    speakCurrent({ at: t - p.duration });
  } else {
    p.currentTime = t;
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
  $("progRow").hidden = true;
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
  showWord(r, ctx);
}

// 「調べる」→「登録」の2段階。登録ボタンを押したときだけ単語帳に入れる
async function register(word, ctx) {
  const r = await post("/api/vocab/add", { w: word, paper: S.paper?.id, sec: ctx.sec, sentence: ctx.sentence });
  showWord(r, ctx);
  loadVocab();
  refreshDue();
}

function showWord(r, ctx = {}) {
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
    box.textContent = "辞書に見つかりませんでした。右下の Weblio で見てください。";
  }
  $("wcSource").textContent = r.found ? r.source : "";
  const reg = $("wcRegister");
  reg.hidden = !r.found;
  reg.disabled = !!r.registered;
  reg.textContent = r.registered ? "登録済み ✓" : "単語帳に登録";
  reg.onclick = () => register(r.query, ctx);
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
      entries: v.meaning.split("\n").map((mean) => ({ word: v.headword, mean })), registered: true,
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
  const sentence = w.closest("[data-t]")?.dataset.t || w.closest(".now")?.textContent;
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

// ---- 画面の切り替え ----
function showView(v) {
  S.view = v;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  $("readView").hidden = v !== "read";
  $("reviewView").hidden = v !== "review";
  $("phoneView").hidden = v !== "phone";
  if (v !== "read") stop();
  if (v === "review") loadReview();
  if (v === "phone") loadPhone();
  pref.set("view", v);
}
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => showView(b.dataset.view)));

// ---- 復習（計算はサーバーの py-fsrs） ----
const RATING_KEYS = { "1": 1, "2": 2, "3": 3, "4": 4 };
function renderCounts(c) {
  $("revCounts").innerHTML = `新しい語 <b class="n">${c.new}</b> ・ 覚えている途中 <b class="l">${c.learning}</b> ・ 復習 <b class="v">${c.review}</b>`
    + ` <span class="muted">（単語帳 ${c.total} 語${c.new_waiting > c.new ? `、明日以降の新しい語 ${c.new_waiting - c.new}` : ""}）</span>`;
  const due = c.new + c.learning + c.review;
  $("dueBadge").hidden = !due;
  $("dueBadge").textContent = due;
}

async function refreshDue() { renderCounts((await api("/api/review")).counts); }

function markWord(text, head) {
  const el = document.createElement("span");
  const re = new RegExp(`\\b(${head.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\w*)`, "i");
  const parts = text.split(re);
  parts.forEach((p, i) => {
    if (i % 2) { const m = document.createElement("mark"); m.textContent = p; el.appendChild(m); }
    else el.appendChild(document.createTextNode(p));
  });
  return el;
}

function renderReview(q) {
  S.review = q;
  renderCounts(q.counts);
  const c = q.card;
  $("revCard").hidden = !c;
  $("revDone").hidden = !!c;
  if (!c) {
    const nd = q.next_due ? new Date(q.next_due) : null;
    $("revDone").textContent = q.counts.total === 0
      ? "単語帳が空です。「読む」で文の中の単語をクリックし、「単語帳に登録」を押すとここに入ります。"
      : `今の分は終わりです。${nd ? `次は ${nd.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} に出ます。` : ""}`;
    return;
  }
  $("revWord").textContent = c.headword;
  const ex = $("revExamples");
  ex.textContent = "";
  for (const e of c.examples) {
    const d = document.createElement("div");
    d.appendChild(markWord(e.sentence, c.headword));
    if (e.audio_ref) {
      const b = document.createElement("button");
      b.className = "small";
      b.textContent = "🔈 文";
      b.onclick = () => { const [pid, item] = e.audio_ref.split("/"); playOnce(`/api/papers/${pid}/audio/${item}.m4a`); };
      d.append(" ", b);
    }
    if (e.section) {
      const s = document.createElement("span");
      s.className = "src";
      s.textContent = e.section;
      d.appendChild(s);
    }
    ex.appendChild(d);
  }
  $("revMeaning").textContent = "";
  for (const line of c.meaning.split(/\n| \/ /)) {
    const d = document.createElement("div");
    d.textContent = line;
    $("revMeaning").appendChild(d);
  }
  $("revSource").textContent = `${c.source || ""} ・ これまで ${c.reviews} 回答えた`;
  document.querySelectorAll("#revButtons button").forEach((b) => { b.querySelector("small").textContent = c.intervals[b.dataset.r]; });
  $("revBack").hidden = true;
  $("revButtons").hidden = true;
  $("revShow").hidden = false;
}

function playOnce(src) { S.wordAudio.src = src; S.wordAudio.play().catch(() => {}); }

async function loadReview() { renderReview(await api("/api/review")); }
function revealAnswer() { if (!S.review?.card) return; $("revBack").hidden = false; $("revButtons").hidden = false; $("revShow").hidden = true; }
async function rate(r) {
  const c = S.review?.card;
  if (!c || $("revButtons").hidden) return;
  renderReview(await post("/api/review/answer", { headword: c.headword, rating: r }));
}
async function undoReview() {
  const q = await post("/api/review/undo");
  renderReview(q);
  if (!q.undone) $("revDone").textContent = "取り消せる答えがありません。";
}
function reviewKey(e) {
  if (e.code === "Space" || e.key === "Enter") { e.preventDefault(); if ($("revButtons").hidden) revealAnswer(); else rate(3); }
  else if (RATING_KEYS[e.key]) rate(RATING_KEYS[e.key]);
  else if (e.key === "z" || e.key === "Z") undoReview();
}
$("revShow").onclick = revealAnswer;
document.querySelectorAll("#revButtons button").forEach((b) => (b.onclick = () => rate(Number(b.dataset.r))));
$("revUndo").onclick = undoReview;
$("revSay").onclick = () => S.review?.card && playOnce(`/api/word_audio?w=${encodeURIComponent(S.review.card.headword)}`);

// ---- スマホ（Android）----
async function loadPhone() {
  // 起動直後にこのタブが開いていても、設定（URL）と論文の一覧を読んでから作る
  if (!S.cfg) await loadConfig();
  if (!S.papers.length) S.papers = await api("/api/papers");
  const url = S.cfg.pwa_url;
  $("pwaLink").href = url;
  $("pwaLink").textContent = url;
  $("pwaQr").innerHTML = await (await fetch(`/api/qr?t=${encodeURIComponent(url)}`)).text();
  const ul = $("sharePapers");
  ul.innerHTML = "";
  const saved = pref.get("sharePapers", null);
  const want = new Set(saved && saved.length ? saved : S.papers.map((m) => m.id));   // 初めては全部に印を付ける
  for (const m of S.papers) {
    const li = document.createElement("li");
    li.innerHTML = `<label><input type="checkbox" value="${m.id}"> <span></span></label>`;
    li.querySelector("input").checked = want.has(m.id);
    li.querySelector("span").textContent = `${m.title}（${m.sentences}文）`;
    li.querySelector("input").onchange = updateBundleLink;
    ul.appendChild(li);
  }
  updateBundleLink();
  renderShare(await api("/api/share"));
}
function sharePicked() { return [...document.querySelectorAll("#sharePapers input:checked")].map((i) => i.value); }
function updateBundleLink() {
  const ids = sharePicked();
  pref.set("sharePapers", ids);
  $("bundleLink").href = `/api/bundle?papers=${ids.join(",")}`;
  $("bundleLink").onclick = (e) => { if (!ids.length) { e.preventDefault(); alert("送る論文に印を付けてください"); } };
}
function renderShare(st) {
  clearInterval(S.sharePoll);
  $("shareBox").hidden = !st.active;
  if (!st.active) return;
  $("shareQr").innerHTML = st.qr;
  $("shareUrl").textContent = st.url;
  $("shareExpire").textContent = `${st.expires_at} まで開いています（${st.zip}、${st.zip_mb} MB）`;
  $("shareReceived").textContent = st.received.length
    ? "スマホから受け取った記録: " + st.received.map((r) => `引いた語 ${r.lookups}・答え ${r.reviews}`).join(" / ") : "";
  S.sharePoll = setInterval(async () => {
    if (S.view !== "phone") return;
    const s2 = await api("/api/share").catch(() => ({ active: false }));
    if (!s2.active || s2.received.length !== st.received.length) { renderShare(s2); refreshDue(); loadVocab(); }
  }, 3000);
}
$("shareStart").onclick = async () => {
  if (!sharePicked().length) { alert("送る論文に印を付けてください"); return; }
  $("shareStart").disabled = true;
  try { renderShare(await post("/api/share/start", { papers: sharePicked() })); }
  catch (e) { alert(e.message); }
  finally { $("shareStart").disabled = false; }
};
$("shareStop").onclick = async () => renderShare(await post("/api/share/stop"));
$("progressInput").onchange = async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  try {
    const r = await api("/api/progress", { method: "POST", body: f });
    $("progressMsg").textContent = `読み込みました: 引いた語 ${r.lookups} 件・答え ${r.reviews} 件を追加`;
    refreshDue(); loadVocab();
  } catch (err) { $("progressMsg").textContent = "読み込めませんでした: " + err.message; }
};

// ---- つなぎこみ ----
$("playAll").onclick = () => S.paper && play(itemsFor(null));
$("pause").onclick = pauseResume;
$("stop").onclick = stop;
$("prev").onclick = () => step(-1);
document.querySelectorAll("[data-seek]").forEach((b) => (b.onclick = () => seekBy(Number(b.dataset.seek))));
// 再生バーのボタンと速さの棒に入力を残さない（押したあとのスペース・← → が、そのボタンや速さに効いてしまうため）
document.querySelectorAll(".player button").forEach((b) => b.addEventListener("mousedown", (e) => e.preventDefault()));
$("speed").addEventListener("change", () => $("speed").blur());
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
$("showJa").checked = pref.get("showJa", true);
$("sections").classList.toggle("hide-ja", !$("showJa").checked);
$("showJa").onchange = () => { pref.set("showJa", $("showJa").checked); $("sections").classList.toggle("hide-ja", !$("showJa").checked); };
$("skipBack").onchange = () => pref.set("skipBack", $("skipBack").checked);
$("importInput").onchange = (e) => { importFiles(e.target.files); e.target.value = ""; };
$("searchForm").onsubmit = (e) => {
  e.preventDefault();
  const w = $("searchInput").value.trim();
  if (w) lookup(w);
};
$("wordClose").onclick = () => { $("wordCard").hidden = true; };
document.addEventListener("keydown", (e) => {
  if (/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  if (!$("lightbox").hidden) {                     // 図を開いている間は ← → Esc を図の操作に使う
    if (e.key === "Escape") closeLightbox();
    else if (e.key === "ArrowLeft") openLightbox(S.lbIndex - 1);
    else if (e.key === "ArrowRight") openLightbox(S.lbIndex + 1);
    e.preventDefault();
    return;
  }
  if (e.key === "Escape") { $("wordCard").hidden = true; return; }
  if (S.view === "review") return reviewKey(e);
  if (e.code === "Space" && e.target.tagName !== "BUTTON") { e.preventDefault(); pauseResume(); }
  if (e.key === "ArrowLeft") { e.preventDefault(); seekBy(e.shiftKey ? -10 : -5); }
  if (e.key === "ArrowRight") { e.preventDefault(); seekBy(e.shiftKey ? 10 : 5); }
});
let dragDepth = 0;
addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("drop").hidden = false; });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("drop").hidden = true; } });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("drop").hidden = true; importFiles(e.dataTransfer.files); });

loadConfig().catch(() => {});
loadVocab().catch(() => {});
refreshDue().catch(() => {});
loadPapers().catch((e) => status("サーバーにつながりません: " + e.message));
showView(pref.get("view", "read"));
