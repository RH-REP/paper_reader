// paper_reader の画面。論文の一覧・取り込み・章ごとの読み上げ・単語検索と単語帳。
// 読み上げは、サーバーが say で作った1文ずつの m4a を順に鳴らす（どのブラウザでも同じ声）。
// 音声がまだできていない間だけ、ブラウザの読み上げ（Web Speech API）で代わりに読む。
import { fillWords as fillShared, labelKey, refKeys, resumeIndex } from "./shared.js";

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

// ---- 文を、クリックできる単語と図への参照に分けて入れる（shared.js）----
function fillWords(el, text) { fillShared(el, text, { terms: S.terms }); }

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
    sm.textContent = (m.source === "text" ? "テキスト" : `${m.pages}ページ`) + `・${m.sections}章・${m.sentences}文`
      + (m.ocr_pages?.length ? `・OCR ${m.ocr_pages.length}ページ` : "") + (m.marks ? `・★${m.marks}` : "");
    li.appendChild(sm);
    const pos = m.position;
    if (pos?.total) {                                // 聞いたところまで（しおり）
      const bar = document.createElement("span");
      bar.className = "pbar";
      bar.title = `しおり: ${pos.section || ""}（${Math.round((pos.done / pos.total) * 100)}%）`;
      bar.innerHTML = `<i style="width:${Math.min(100, (pos.done / pos.total) * 100).toFixed(1)}%"></i>`;
      li.appendChild(bar);
    }
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
  S.openSecs = new Set();
  S.marks = p.marks || [];
  S.position = p.position || null;
  setTerms(p.glossary);
  renderMeta();
  $("aiBtn").hidden = !p.has_pdf;                  // 貼り付けたテキストには元の PDF が無い
  renderSections();
  renderAudio();
  watchAudio();
  renderTranslation();
  watchTranslation();
  $("aiPanel").hidden = true;
  loadFigures();
  renderMarks();
  renderResume();
  renderTerms();
  watchTerms();
  renderQuality();
  if (p.ai_fix?.state === "running") watchAiFix();
}

function renderMeta() {
  const p = S.paper;
  const meta = S.papers.find((m) => m.id === p.id) || {};
  $("paperMeta").textContent = (p.has_pdf ? `${meta.source_name || ""} ・ ${meta.pages ?? p.pages ?? "?"}ページ` : "貼り付けたテキスト")
    + ` ・ ${p.sections.length}章` + (p.ocr_pages.length ? ` ・ OCR したページ: ${p.ocr_pages.join(", ")}` : "");
  const m = p.manual;
  const head = !m ? "" : `${m.by === "user" ? "画面で編集済み" : "AI 手直し済み"}（${(m.edited_at || m.at || "").slice(0, 16).replace("T", " ")}）`;
  const notes = m?.notes && m.by !== "user" ? m.notes : "";
  const tag = $("manualTag");                       // AI の手直しの説明は長いので、押すと全部出す
  tag.textContent = head + (notes ? `: ${notes.length > 60 ? notes.slice(0, 60) + "…" : notes}` : "");
  tag.title = notes;
  tag.style.cursor = notes.length > 60 ? "pointer" : "";
  tag.onclick = () => { if (notes.length > 60) tag.textContent = tag.textContent.endsWith("…") ? `${head}: ${notes}` : `${head}: ${notes.slice(0, 60)}…`; };
}

// ---- 図・表・数式（画像として開く）----
async function loadFigures() {
  const id = S.paper.id;
  $("figPanel").hidden = true;
  $("figBtn").hidden = true;
  const st = await api(`/api/papers/${id}/figures`).catch(() => ({ items: [] }));
  if (!S.paper || S.paper.id !== id) return;
  S.figures = (st.items || []).map((it) => ({ ...it, url: `/api/papers/${id}/figures/${it.file}` }));
  S.figKeys = new Map();
  S.figures.forEach((f, i) => { const k = labelKey(f.label); if (k && !S.figKeys.has(k)) S.figKeys.set(k, i); });
  markRefs();
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
  ol.classList.toggle("editing", !!S.editing);
  for (const [i, sec] of p.sections.entries()) {
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
    count.textContent = `${sec.sentences.length}文` + (p.has_pdf ? ` ・ p.${sec.page}` : "");
    head.append(btn, name, count);
    if (S.editing) head.appendChild(editControls(sec, i));
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
      det.open = S.openSecs?.has(sec.id);
      det.ontoggle = () => { det.open ? S.openSecs.add(sec.id) : S.openSecs.delete(sec.id); };
      const sum = document.createElement("summary");
      sum.textContent = "文を見る（単語をクリックで辞書）";
      const list = document.createElement("ol");
      sec.sentences.forEach((s, k) => {
        const x = document.createElement("li");
        x.dataset.t = s.t;                             // 辞書に渡す文（▶ の文字を混ぜない）
        x.appendChild(markButton(s.t, sec.title));
        x.classList.toggle("marked", isMarked(s.t));
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
        if (S.editing) {
          const c = document.createElement("button");
          c.className = "cut";
          c.textContent = "ここで分ける";
          c.title = "この文から新しい章にする（短い文なら、その文を見出しにできる）";
          c.onclick = () => splitAt(i, k);
          x.appendChild(c);
        }
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

// ---- しおり（聞いた位置）----
// 位置は文の中身で覚える（章の編集や AI の手直しで番号がずれても戻れる）。文が変わるたび・一時停止・停止で保存する
function savePosition(force = false, finished = false) {
  if (!S.paper || !S.items.length || S.pos >= S.items.length && !finished) return;
  if (S.items.length < 2) return;                  // 1文だけの再生（文の ▶・印から）ではしおりを動かさない
  const it = S.items[Math.min(S.pos, S.items.length - 1)];
  const now = Date.now();
  if (!force && S.lastSave && now - S.lastSave < 4000 && S.lastSaveId === it.id) return;
  S.lastSave = now;
  S.lastSaveId = it.id;
  const full = itemsFor(null);
  const idx = Math.max(0, full.findIndex((x) => x.id === it.id));
  const pos = { paper_id: S.paper.id, sentence: it.show, section: it.sec.title, item_id: it.id,
                offset: finished ? 0 : Math.round((S.player.currentTime || 0) * 10) / 10,
                done: finished ? full.length : idx, total: full.length };
  S.position = pos;
  const m = S.papers.find((x) => x.id === S.paper.id);
  if (m) m.position = pos;
  post("/api/position", pos).catch(() => {});
}
addEventListener("pagehide", () => {
  if (!S.playing || !S.items.length) return;
  const it = S.items[S.pos];
  if (!it) return;
  const full = itemsFor(null);
  navigator.sendBeacon?.("/api/position", new Blob([JSON.stringify({ paper_id: S.paper.id, sentence: it.show, section: it.sec.title,
    item_id: it.id, offset: S.player.currentTime || 0, done: Math.max(0, full.findIndex((x) => x.id === it.id)), total: full.length })],
    { type: "application/json" }));
});

function renderResume() {
  const pos = S.position;
  const can = pos && pos.total && pos.done < pos.total && !S.playing;
  $("playAll").textContent = can ? `▶ 続きから（${pos.section || ""}・${Math.round((pos.done / pos.total) * 100)}%）` : "▶ 全体を読む";
  $("playAll").title = can ? `しおり: ${pos.sentence.slice(0, 80)}` : "最初から全体を読む";
  $("playTop").hidden = !can;
}

function playAllOrResume(fromTop = false) {
  if (!S.paper) return;
  const items = itemsFor(null);
  const pos = S.position;
  if (fromTop || !pos || !pos.total || pos.done >= pos.total) return play(items);
  const r = resumeIndex(items, pos);
  play(items, r.index, { at: r.exact ? Math.max(0, r.at - 1) : 0 });   // 1秒手前から
}

// ---- 自動の取り出しの点検と、AI の手直し（この Mac の Claude Code）----
const elapsed = (from) => { const s = Math.max(0, Math.round((Date.now() - new Date(from).getTime()) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

function renderQuality() {
  const p = S.paper, q = p.quality || { major: 0, minor: 0, top: [] }, a = p.ai_fix || { state: "none" };
  const box = $("qualityBox");
  box.className = "qualitybox";
  box.textContent = "";
  if (a.state === "running") {
    box.classList.add("run");
    box.innerHTML = `<b>AI が手直ししています</b>（経過 ${elapsed(a.started_at)}・操作 ${a.tools} 回）<br><span class="muted"></span>
      <div><button class="small" id="aiStop">止める</button></div>`;
    box.querySelector(".muted").textContent = a.last || "";
    box.querySelector("#aiStop").onclick = async () => { await post(`/api/papers/${p.id}/ai_fix`, { action: "stop" }).catch(() => {}); };
    box.hidden = false;
    return;
  }
  const justDone = ["done", "error", "stopped"].includes(a.state) && S.aiShown !== a.finished_at;
  if (justDone || (["done", "error"].includes(a.state) && S.showAiResult)) {
    box.classList.add(a.state === "done" ? "ok" : "");
    const title = { done: "AI の手直しが終わりました", error: "AI の手直しが途中で止まりました", stopped: "AI の手直しを止めました" }[a.state];
    box.innerHTML = `<b></b> <span class="muted"></span><details><summary>AI の報告</summary><pre></pre></details>
      <div><button class="small" id="aiOk">閉じる</button></div>`;
    box.querySelector("b").textContent = title;
    box.querySelector(".muted").textContent = `（${(a.finished_at || "").slice(11, 16)}・操作 ${a.tools} 回${a.cost_usd != null ? `・約 $${Number(a.cost_usd).toFixed(2)}` : ""}）`;
    box.querySelector("pre").textContent = a.result || "（報告なし。data/papers/<id>/ai_fix.log を見てください）";
    box.querySelector("#aiOk").onclick = () => { S.aiShown = a.finished_at; S.showAiResult = false; renderQuality(); };
    box.hidden = false;
    return;
  }
  if (p.manual || !q.recommend) { box.hidden = true; return; }
  box.innerHTML = `<b>自動の取り出しに気になる所があります</b>（重要 ${q.major}・軽い ${q.minor}）。AI に手直しさせると直ります。<ul></ul><div></div>`;
  for (const i of q.top) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="${i.level === "major" ? "maj" : ""}"></span>`;
    li.firstChild.textContent = (i.where ? `${i.where}: ` : "") + i.msg;
    box.querySelector("ul").appendChild(li);
  }
  const row = box.querySelector("div");
  if (a.available) {
    const b = document.createElement("button");
    b.className = "primary";
    b.textContent = "この Mac の Claude Code に手直しを頼む";
    b.title = "claude -p を裏で動かし、元の PDF を見ながら章と文・図を直させます（数分〜十数分）";
    b.onclick = startAiFix;
    row.appendChild(b);
  }
  const c = document.createElement("button");
  c.className = "small";
  c.textContent = "プロンプトをコピーして自分で頼む";
  c.onclick = () => $("aiBtn").click();
  row.append(" ", c);
  box.hidden = false;
}

async function startAiFix() {
  if (!confirm("この論文の PDF と取り出した文を Claude（Anthropic）に送り、手直しさせます。\n"
    + "Claude Code の利用料がかかります（論文1本で数ドル程度）。終わるまで数分〜十数分かかります。\n\n始めますか？")) return;
  try {
    const st = await post(`/api/papers/${S.paper.id}/ai_fix`, { action: "start" });
    S.paper.ai_fix = { ...st, available: true };
  } catch (e) { alert(`始められませんでした: ${e.message}`); return; }
  renderQuality();
  watchAiFix();
}

function watchAiFix() {
  clearInterval(S.aiPoll);
  const id = S.paper.id;
  S.aiPoll = setInterval(async () => {
    if (!S.paper || S.paper.id !== id) return clearInterval(S.aiPoll);
    const st = await api(`/api/papers/${id}/ai_fix`).catch(() => null);
    if (!st) return;
    S.paper.ai_fix = st;
    if (st.state !== "running") {
      clearInterval(S.aiPoll);
      S.showAiResult = true;
      if (!S.playing) { await openPaper(id); S.showAiResult = true; renderQuality(); }   // 手直しを画面に反映（音声・訳は変わった文だけ作り直し）
      else renderQuality();
      return;
    }
    renderQuality();
  }, 2000);
}

// ---- 専門用語 ----
function setTerms(gl) {
  S.glossary = gl || { state: "none", terms: [] };
  S.terms = new Set();
  for (const t of S.glossary.terms || []) {
    if (t.kind === "phrase") continue;
    const k = t.term.toLowerCase();
    S.terms.add(k);
    if (t.kind === "word") { S.terms.add(k + "s"); if (k.endsWith("y")) S.terms.add(k.slice(0, -1) + "ies"); }
    else S.terms.add(k + "s");                      // DMs
  }
}

function renderTerms() {
  const g = S.glossary || { terms: [] };
  const terms = g.terms || [];
  $("termsBtn").hidden = !terms.length && !["none", "running"].includes(g.state) && g.current !== false;
  $("termsBtn").textContent = terms.length ? `用語（${terms.length}）` : "用語（作成中）";
  const body = $("termsBody");
  body.textContent = "";
  if (!terms.length) { body.innerHTML = '<p class="muted">用語の一覧を作っています…</p>'; return; }
  if (g.state === "no_translation") body.insertAdjacentHTML("beforeend", '<p class="muted">訳を作れなかったので、語の一覧だけです（あとで自動で作り直します）。</p>');
  const groups = [["acronym", "略語"], ["word", "専門の語（辞書に無い・派生語でしか引けない）"], ["phrase", "よく出る句"]];
  for (const [kind, name] of groups) {
    const list = terms.filter((t) => t.kind === kind);
    if (!list.length) continue;
    const tbl = document.createElement("table");
    tbl.className = "termtable";
    tbl.innerHTML = `<thead><tr><th>${name}</th><th>訳</th><th class="n">回数</th><th></th></tr></thead><tbody></tbody>`;
    for (const t of list) {
      const tr = document.createElement("tr");
      const reg = S.vocabSet?.has(t.term.toLowerCase());
      tr.innerHTML = '<td><span class="tm"></span><div class="ex"></div></td><td class="ja"></td><td class="n"></td><td></td>';
      tr.querySelector(".tm").textContent = t.term;
      tr.querySelector(".tm").onclick = () => lookup(t.kind === "phrase" ? t.term.split(" ").pop() : t.term, { sec: t.section, sentence: t.sentence });
      tr.querySelector(".ex").textContent = t.expansion || "";
      tr.querySelector(".ex").title = t.sentence || "";
      tr.querySelector(".ja").textContent = t.ja || "";
      tr.querySelector(".n").textContent = t.count;
      if (t.kind !== "phrase") {
        const b = document.createElement("button");
        b.className = "small";
        b.textContent = reg ? "登録済み ✓" : "登録";
        b.disabled = !!reg || !t.ja;
        b.onclick = async () => { await register(t.term, { sec: t.section, sentence: t.sentence }).catch(() => {}); renderTerms(); };
        tr.lastChild.appendChild(b);
      }
      tbl.tBodies[0].appendChild(tr);
    }
    body.appendChild(tbl);
  }
}

function watchTerms() {
  clearInterval(S.termPoll);
  if (S.glossary?.current && S.glossary.state !== "none") return;
  const id = S.paper.id;
  let n = 0;
  S.termPoll = setInterval(async () => {
    if (!S.paper || S.paper.id !== id || ++n > 60) return clearInterval(S.termPoll);
    const g = await api(`/api/papers/${id}/glossary`).catch(() => null);
    if (!g || !g.current || g.state === "none") return;
    clearInterval(S.termPoll);
    setTerms(g);
    renderTerms();
    renderSections();                               // 本文の専門の語に点線を付ける
  }, 2000);
}

// ---- 文の印（★）----
function isMarked(t) { return (S.marks || []).some((m) => m.sentence === t); }

function markButton(t, section) {
  const b = document.createElement("button");
  const on = isMarked(t);
  b.className = "mk" + (on ? " on" : "");
  b.textContent = on ? "★" : "☆";
  b.title = on ? "印を外す" : "この文に印を付ける";
  b.onclick = (e) => { e.stopPropagation(); toggleMark(t, section); };
  return b;
}

async function toggleMark(t, section) {
  if (!S.paper || !t) return;
  const r = await post("/api/marks/toggle", { paper_id: S.paper.id, sentence: t, section });
  S.marks = r.marks;
  const m = S.papers.find((x) => x.id === S.paper.id);
  if (m) m.marks = r.marks.length;
  document.querySelectorAll("#sections li[data-t]").forEach((li) => {
    if (li.dataset.t !== t) return;
    li.classList.toggle("marked", !!r.mark);
    li.querySelector(".mk").replaceWith(markButton(t, section));
  });
  if (S.playing && S.items[S.pos]?.show === t) { $("nowMark").classList.toggle("on", !!r.mark); $("nowMark").textContent = r.mark ? "★" : "☆"; }
  renderMarks();
}

function renderMarks() {
  const marks = S.marks || [];
  $("marksBtn").hidden = !marks.length && $("marksPanel").hidden;
  $("marksBtn").textContent = `★ 印（${marks.length}）`;
  const ol = $("marksList");
  ol.textContent = "";
  const where = new Map();                         // 文 → [章, 訳, 読み上げの並びの位置]
  const full = itemsFor(null);
  S.paper.sections.forEach((sec) => sec.sentences.forEach((s, k) => {
    if (!where.has(s.t)) where.set(s.t, [sec, S.paper.ja?.[`${sec.id}_${k + 1}`] || "", full.findIndex((x) => x.id === `${sec.id}_${k + 1}`)]);
  }));
  const order = (m) => (where.has(m.sentence) ? S.paper.sections.indexOf(where.get(m.sentence)[0]) * 1e4 + where.get(m.sentence)[2] : 1e9);
  for (const m of [...marks].sort((a, b) => order(a) - order(b))) {
    const [sec, ja, idx] = where.get(m.sentence) || [null, "", -1];
    const li = document.createElement("li");
    const w = document.createElement("div");
    w.className = "where";
    w.textContent = sec ? sec.title : `${m.section || ""}（今の文には見つからない）`;
    const en = document.createElement("div");
    en.className = "en";
    en.textContent = m.sentence;
    en.title = "この文から続けて再生";
    en.onclick = () => { if (idx >= 0) play(full, idx); };
    li.append(w, en);
    if (ja) { const j = document.createElement("div"); j.className = "ja"; j.textContent = ja; li.appendChild(j); }
    const note = document.createElement("textarea");
    note.rows = 1;
    note.placeholder = "メモ（申請書に使う観点など）";
    note.value = m.note || "";
    note.onchange = async () => { const r = await post(`/api/marks/${m.uid}/note`, { note: note.value }).catch(() => null); if (r) m.note = r.note; };
    const del = document.createElement("button");
    del.className = "small";
    del.textContent = "印を外す";
    del.onclick = () => toggleMark(m.sentence, m.section);
    li.append(note, del);
    ol.appendChild(li);
  }
  if (!marks.length) ol.innerHTML = '<li class="muted">まだ印はありません。</li>';
}

async function marksMarkdown() {
  const q = $("marksAll").checked ? "" : `?paper=${S.paper.id}`;
  const r = await fetch(`/api/marks/export${q}`);
  if (!r.ok) throw new Error(r.statusText);
  return r.text();
}

// 図の一覧に無い参照は、押せない見た目にする
function markRefs(root = document) {
  root.querySelectorAll("a.fr").forEach((a) => a.classList.toggle("missing", !S.figKeys?.has(a.dataset.key)));
}

// ---- 章の編集（見出しの段・分ける・つなぐ・名前）----
function editControls(sec, i) {
  const box = document.createElement("span");
  box.className = "ed";
  const b = (text, title, fn, disabled) => {
    const x = document.createElement("button");
    x.textContent = text;
    x.title = title;
    x.disabled = !!disabled;
    x.onclick = fn;
    box.appendChild(x);
  };
  const secs = S.paper.sections;
  b("<", "この章と下の節を1段上げる", () => editSections({ op: "outdent", sec: i }), sec.level <= 1);
  b(">", "この章と下の節を1段下げる", () => editSections({ op: "indent", sec: i }),
    i === 0 || sec.level > secs[i - 1].level || sec.level >= 3);
  box.firstChild.classList.add("lv");
  box.children[1].classList.add("lv");
  b("名前", "見出しを変える", () => {
    const t = prompt("見出し", sec.title);
    if (t && t.trim() && t.trim() !== sec.title) editSections({ op: "rename", sec: i, title: t.trim() });
  });
  b("前とつなぐ", "見出しを文に戻して、前の章に入れる", () => editSections({ op: "merge", sec: i }), i === 0);
  return box;
}

function splitAt(i, k) {
  const t = S.paper.sections[i].sentences[k].t.trim();
  const short = t.split(/\s+/).length <= 10;
  const guess = short ? t.replace(/[.:]\s*$/, "") : "";
  const title = prompt(short ? "新しい章の見出し（そのままなら、この文が見出しになります）"
                             : "新しい章の見出し（この文から下が新しい章になります）", guess);
  if (title === null || !title.trim()) return;
  const take = short && title.trim() === guess;      // 見出しにした文は本文から外す
  if (!take && k === 0) return alert("章の最初の文の前では分けられません（見出しを変えるなら「名前」）");
  editSections({ op: "split", sec: i, sentence: k, take, title: title.trim() });
}

async function editSections(body) {
  stop();
  $("editMsg").textContent = "";
  const y = scrollY;
  let p;
  try { p = await post(`/api/papers/${S.paper.id}/sections`, body); }
  catch (e) { $("editMsg").textContent = ` ${e.message}`; return; }
  const keep = S.openSecs;
  S.paper = p;
  S.audio = p.audio;
  setTerms(p.glossary);
  watchTerms();
  S.openSecs = keep;
  const m = S.papers.find((x) => x.id === p.id);
  if (m) { m.sections = p.sections.length; m.sentences = p.sections.reduce((n, s) => n + s.sentences.length, 0); }
  renderMeta();
  renderSections();
  renderAudio();
  watchAudio();
  renderTranslation();
  watchTranslation();
  scrollTo(0, y);
}

function setEditing(on) {
  S.editing = on;
  $("editBtn").setAttribute("aria-pressed", String(on));
  $("editHelp").hidden = !on;
  $("editMsg").textContent = "";
  if (S.paper) renderSections();
}

// ---- テキストを貼り付けて取り込む ----
async function importText(title, text) {
  $("pasteMsg").textContent = "";
  let m;
  try { m = await post("/api/import_text", { title, text }); }
  catch (e) { $("pasteMsg").textContent = e.message; return false; }
  $("importMsg").textContent = `${m.already ? "既にあります" : "取り込みました（続けて音声と訳を作ります）"}: ${m.title}`;
  await loadPapers(m.id);
  return true;
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
function play(items, start = 0, opts = {}) {
  stop();
  if (!items.length) return;
  S.items = items;
  S.pos = Math.min(start, items.length - 1);
  S.playing = true;
  S.paused = false;
  buildTimeline();
  $("progRow").hidden = false;
  renderResume();
  speakCurrent(opts.at ? { at: opts.at } : {});
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
  if (S.pos >= S.items.length) { S.playing = false; status("読み終わりました"); $("nowRow").hidden = true; savePosition(true, true); return; }
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
    savePosition(true);
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
  savePosition(true);
  S.gen++;
  S.playing = false;
  S.paused = false;
  S.items = [];
  haltOutput();
  status("停止中");
  $("pause").textContent = "⏸";
  $("nowRow").hidden = true;
  $("progRow").hidden = true;
  renderResume();
}

function showProgress() {
  const it = S.items[S.pos];
  const where = it.heading ? "見出し" : `${it.n} / ${it.sec.sentences.length} 文`;
  status(`${S.paused ? "一時停止中 — " : ""}${it.sec.title} ・ ${where}（全体 ${S.pos + 1} / ${S.items.length}）`);
  $("pause").textContent = S.paused ? "▶" : "⏸";
  const now = $("nowText");
  $("nowRow").hidden = false;
  now.dataset.sec = it.sec.id;
  now.dataset.t = it.heading ? "" : it.show;
  fillWords(now, it.show);
  markRefs(now);
  $("nowMark").hidden = !!it.heading;
  $("nowMark").classList.toggle("on", !it.heading && isMarked(it.show));
  $("nowMark").textContent = !it.heading && isMarked(it.show) ? "★" : "☆";
  const fk = it.heading ? [] : refKeys(it.show).filter((k) => S.figKeys?.has(k));
  const img = $("nowFig");                          // この文が指す図を横に出す
  img.hidden = !fk.length;
  if (fk.length) {
    const f = S.figures[S.figKeys.get(fk[0])];
    if (img.dataset.src !== f.url) { img.src = f.url; img.dataset.src = f.url; }
    img.alt = img.title = `${f.label}（押すと大きく開く）`;
    img.onclick = () => openLightbox(S.figKeys.get(fk[0]));
  }
  savePosition(S.paused);
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
  showReading(r.query);
}

// ---- 読み方の辞書 ----
async function loadPron() { S.pron = (await api("/api/pronounce")).rules; return S.pron; }

async function showReading(word) {
  const rules = S.pron || await loadPron().catch(() => []);
  const hit = rules.find((x) => (x.case ? x.from === word : x.from.toLowerCase() === word.toLowerCase()));
  $("wcRead").textContent = hit ? `読み方: 「${hit.to}」` : "";
  $("wcReadEdit").textContent = hit ? "読み方を変える" : "読み方を直す";
  $("wcReadEdit").onclick = async () => {
    const to = prompt(`「${word}」を読み上げでどう読むか（空にすると辞書から消す）。例: DM → D M、ALPAO → al pao`, hit ? hit.to : word);
    if (to === null) return;
    const rest = rules.filter((x) => x !== hit);
    await savePron(to.trim() ? [...rest, { from: word, to: to.trim(), case: /[A-Z]/.test(word) }] : rest);
    showReading(word);
    const pv = await post("/api/pronounce/preview", { text: word }).catch(() => null);
    if (pv) { S.wordAudio.src = pv.url; S.wordAudio.play().catch(() => {}); }
  };
}

async function savePron(rules) {
  S.pron = (await post("/api/pronounce", { rules })).rules;
  if (S.paper) {                                   // 変わった文だけ作り直しが始まる
    const p = await api(`/api/papers/${S.paper.id}`).catch(() => null);
    if (p && S.paper?.id === p.id) { S.audio = p.audio; renderAudio(); watchAudio(); }
  }
  return S.pron;
}

function pronRow(r = { from: "", to: "", case: true }) {
  const tr = document.createElement("tr");
  tr.innerHTML = '<td><input type="text" class="pf" spellcheck="false"></td><td><input type="text" class="pt" spellcheck="false"></td>'
    + '<td><input type="checkbox" class="pc"></td><td><button class="small ps" title="試聴">🔈</button> <button class="small px" title="消す">✕</button></td>';
  tr.querySelector(".pf").value = r.from;
  tr.querySelector(".pt").value = r.to;
  tr.querySelector(".pc").checked = !!r.case;
  tr.querySelector(".px").onclick = () => tr.remove();
  tr.querySelector(".ps").onclick = () => tryPron(tr.querySelector(".pt").value);
  return tr;
}
function pronFromRows() {
  return [...document.querySelectorAll("#pronRows tr")].map((tr) => ({ from: tr.querySelector(".pf").value.trim(),
    to: tr.querySelector(".pt").value.trim(), case: tr.querySelector(".pc").checked })).filter((r) => r.from && r.to);
}
async function tryPron(text) {
  if (!text.trim()) return;
  $("pronSpoken").textContent = "試聴の音声を作っています…";
  const pv = await post("/api/pronounce/preview", { text }).catch((e) => ({ error: e.message }));
  $("pronSpoken").textContent = pv.error ? pv.error : `読み: ${pv.spoken}`;
  if (pv.url) { S.wordAudio.src = pv.url; S.wordAudio.play().catch(() => {}); }
}
$("pronBtn").onclick = async () => {
  const rows = $("pronRows");
  rows.textContent = "";
  for (const r of await loadPron()) rows.appendChild(pronRow(r));
  if (!rows.children.length) rows.appendChild(pronRow());
  $("pronMsg").textContent = "";
  $("pronDlg").showModal();
};
$("pronAdd").onclick = () => $("pronRows").appendChild(pronRow());
$("pronClose").onclick = () => $("pronDlg").close();
$("pronTryBtn").onclick = async () => {
  // 保存前の行でも試せるよう、一度保存してから試聴する
  await savePron(pronFromRows());
  tryPron($("pronTry").value);
};
$("pronSave").onclick = async () => {
  const saved = await savePron(pronFromRows());
  $("pronMsg").textContent = `保存しました（${saved.length} 件）。変わった文の音声を作り直します`;
};

async function loadVocab() {
  const vs = await api("/api/vocab");
  S.vocabSet = new Set(vs.map((v) => v.headword.toLowerCase()));
  if (S.paper && !$("termsPanel").hidden) renderTerms();
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
  const fr = e.target.closest("a.fr");
  if (fr) { e.preventDefault(); if (S.figKeys?.has(fr.dataset.key)) openLightbox(S.figKeys.get(fr.dataset.key)); return; }
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
$("playAll").onclick = () => playAllOrResume();
$("playTop").onclick = () => playAllOrResume(true);
$("nowMark").onclick = () => { const it = S.items[S.pos]; if (it && !it.heading) toggleMark(it.show, it.sec.title); };
$("marksBtn").onclick = () => { $("marksPanel").hidden = !$("marksPanel").hidden; };
$("termsBtn").onclick = () => { $("termsPanel").hidden = !$("termsPanel").hidden; if (!$("termsPanel").hidden) renderTerms(); };
$("marksCopy").onclick = async () => {
  try { await navigator.clipboard.writeText(await marksMarkdown()); $("marksMsg").textContent = "コピーしました"; }
  catch (e) { $("marksMsg").textContent = `コピーできませんでした: ${e.message}`; }
};
$("marksSave").onclick = async () => {
  const text = await marksMarkdown();
  const a = document.createElement("a");
  const d = new Date(), z = (n) => String(n).padStart(2, "0");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  a.download = `marks_${$("marksAll").checked ? "all" : S.paper.id}_${d.getFullYear() % 100}${z(d.getMonth() + 1)}${z(d.getDate())}.md`;
  a.click();
  $("marksMsg").textContent = `保存しました（${a.download}）`;
};
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
$("editBtn").onclick = () => setEditing(!S.editing);
$("pasteBtn").onclick = () => { $("pasteMsg").textContent = ""; $("pasteDlg").showModal(); $("pasteText").focus(); };
$("pasteCancel").onclick = (e) => { e.preventDefault(); $("pasteDlg").close(); };
$("pasteForm").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("pasteText").value.trim();
  if (!text) { $("pasteMsg").textContent = "本文が空です"; return; }
  $("pasteOk").disabled = true;
  const ok = await importText($("pasteTitle").value.trim(), text);
  $("pasteOk").disabled = false;
  if (ok) { $("pasteDlg").close(); $("pasteTitle").value = ""; $("pasteText").value = ""; }
};
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
  if ((e.key === "m" || e.key === "M") && S.playing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); $("nowMark").click(); }
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
