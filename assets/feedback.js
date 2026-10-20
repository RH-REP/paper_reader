// 画面の !○（改善のコメント）。どの画面からでも「画面 ID ＋ コメント ＋ 画面の写真 ＋ 記録」を GitHub の Issue にする。
// ほかの app にもそのまま写せる部品（正本は assets/feedback.js。スマホ版は pwa/js/feedback.js に同じものを置き、テストで同じか確かめる）。
//
// 使い方:
//   installFeedback({ app: "paper_reader", version: "…", repo: "OWNER/REPO",
//                     getContext: () => ({ paper, sentence, device, extra }),   // Issue に書く今の状態（無ければ {}）
//                     getLog: () => "…",                                       // 画面側の記録（再生の記録など）
//                     send: async (payload) => ({ number, url }) })            // 送り方（Mac: サーバーへ／スマホ: GitHub の画面を開く）
// 画面 ID: 要素に data-screen="reader.dictation" を付ける。見えているもののうち、data-screen-layer の大きいもの
//          （同じなら DOM の深いもの）を今の画面とする。ダイアログ・パネルは layer を大きくする。

const ERR_KEY = "feedback.errors";

// ---- 画面のエラーを覚えておく（改善のコメントに付ける。最近の 30 件）----
function rememberError(msg) {
  try {
    const a = JSON.parse(localStorage.getItem(ERR_KEY) || "[]");
    a.push(`${new Date().toISOString()} ${String(msg).slice(0, 300)}`);
    localStorage.setItem(ERR_KEY, JSON.stringify(a.slice(-30)));
  } catch { /* 保存できなくても続ける */ }
}
export function errorLog() {
  try { return JSON.parse(localStorage.getItem(ERR_KEY) || "[]").join("\n"); } catch { return ""; }
}

// ---- 今の画面 ID ----
function visible(el) {
  if (el.closest("[hidden]")) return false;
  if (el.checkVisibility) return el.checkVisibility();
  const st = getComputedStyle(el);
  return st.display !== "none" && st.visibility !== "hidden" && el.getClientRects().length > 0;
}
export function currentScreen(root = document) {
  let best = null, bestLayer = -Infinity, bestDepth = -1;
  for (const el of root.querySelectorAll("[data-screen]")) {
    if (el.tagName === "DIALOG" && !el.open) continue;
    if (!visible(el)) continue;
    const layer = Number(el.dataset.screenLayer || 0);
    let depth = 0;
    for (let p = el; p; p = p.parentElement) depth++;
    if (layer > bestLayer || (layer === bestLayer && depth > bestDepth)) { best = el; bestLayer = layer; bestDepth = depth; }
  }
  return best ? best.dataset.screen : "unknown";
}

// ---- 画面の写真（見えている範囲を、HTML を絵に描き直して PNG にする。許可のダイアログは出ない）----
// 描き直しなので、動画・一部の飾りは実物と違うことがある。描けないブラウザでは null（貼り付けた写真で代わりにできる）
async function toDataURL(url) {
  const blob = await (await fetch(url)).blob();
  return new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => ok(url); r.readAsDataURL(blob); });
}
export async function captureScreen({ exclude = [], maxWidth = 1400 } = {}) {
  try {
    const W = innerWidth, H = innerHeight;
    let css = "";
    for (const sh of document.styleSheets) { try { for (const r of sh.cssRules) css += r.cssText + "\n"; } catch { /* 別の生成元 */ } }
    const src = document.body, clone = src.cloneNode(true);
    const a = [src, ...src.querySelectorAll("*")], b = [clone, ...clone.querySelectorAll("*")];
    const jobs = [];
    for (let i = 0; i < a.length; i++) {
      const s = a[i], d = b[i];
      if (!d) continue;
      if (s.tagName === "INPUT") { if (s.type === "checkbox" || s.type === "radio") { if (s.checked) d.setAttribute("checked", ""); } else d.setAttribute("value", s.value); }
      else if (s.tagName === "TEXTAREA") d.textContent = s.value;
      else if (s.tagName === "SELECT") { const o = d.options?.[s.selectedIndex]; if (o) o.setAttribute("selected", ""); }
      else if (s.tagName === "CANVAS") { try { const im = document.createElement("img"); im.src = s.toDataURL(); im.style.cssText = s.style.cssText; d.replaceWith(im); } catch { /* */ } }
      else if (s.tagName === "IMG" && s.currentSrc && visible(s)) jobs.push(toDataURL(s.currentSrc).then((u) => d.setAttribute("src", u)).catch(() => d.removeAttribute("src")));
      else if (s.tagName === "IMG") d.removeAttribute("src");
      if (s.scrollTop > 0 && s !== src) {               // 中でスクロールしている所は、今見えている位置で描く
        const sh = document.createElement("div");
        sh.style.cssText = `margin-top:-${s.scrollTop}px;height:0`;
        d.prepend(sh);
      }
      if (s.tagName === "DIALOG" && s.open) d.setAttribute("open", "");
    }
    for (const sel of exclude) clone.querySelectorAll(sel).forEach((x) => x.remove());
    clone.querySelectorAll("script,audio,video").forEach((x) => x.remove());
    await Promise.all(jobs);
    clone.style.position = "relative";
    clone.style.top = `-${scrollY}px`;
    clone.style.margin = "0";
    const html = document.documentElement;
    const bg = getComputedStyle(document.body).backgroundColor;
    const body = new XMLSerializer().serializeToString(clone);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}"><foreignObject x="0" y="0" width="${W}" height="${H}">` +
      `<div xmlns="http://www.w3.org/1999/xhtml" class="${html.className}" ${html.dataset.theme ? `data-theme="${html.dataset.theme}"` : ""} ` +
      `style="width:${W}px;height:${H}px;overflow:hidden;background:${bg}"><style>${css.replace(/<\/style/gi, "")}</style>${body}</div></foreignObject></svg>`;
    const img = new Image();
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    await img.decode();
    const scale = Math.min(maxWidth / W, Math.min(2, devicePixelRatio || 1));
    const c = document.createElement("canvas");
    c.width = Math.round(W * scale);
    c.height = Math.round(H * scale);
    const ctx = c.getContext("2d");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    return c.toDataURL("image/png");
  } catch (e) {
    rememberError(`画面の写真を撮れなかった: ${e?.message || e}`);
    return null;
  }
}

// ---- Issue の本文（feedback.py の issue_body と同じ形。スマホ版はこれで GitHub の画面を開く）----
export function issueTitle(r) {
  const first = (r.comment || "").trim().split("\n")[0] || "（コメントなし）";
  return `[${r.screen}] ${first.slice(0, 70)}`;
}
export function issueBody(r) {
  const c = r.context || {};
  const L = [`**画面**: \`${r.screen}\``, `**app**: ${r.app} ・ 版 ${r.version} ・ ${c.device || ""}`, `**時刻**: ${r.at || new Date().toISOString()}`];
  if (c.paper) L.push(`**論文・記事**: ${c.paper}`);
  if (c.sentence) L.push(`**今の文**: > ${c.sentence}`);
  if (c.extra) L.push(`**そのときの状態**: ${c.extra}`);
  L.push("", "### コメント", "", (r.comment || "").trim() || "（なし）");
  if (r.imageNote) L.push("", "### 画面の写真", "", r.imageNote);
  if (r.log) L.push("", "<details><summary>システムの記録（末尾）</summary>", "", "```", r.log.trimEnd(), "```", "</details>");
  L.push("", "<!-- 画面の !○ から送った -->");
  return L.join("\n");
}
// GitHub の「新しい Issue」画面の URL（書き込み済み）。長すぎると開けないので、記録 → 今の文の順に削る
export function newIssueUrl(repo, r, maxLen = 7000) {
  const build = (x) => `https://github.com/${repo}/issues/new?labels=feedback&title=${encodeURIComponent(issueTitle(x))}&body=${encodeURIComponent(issueBody(x))}`;
  let x = { ...r };
  let url = build(x);
  while (url.length > maxLen && x.log) { x = { ...x, log: x.log.length > 400 ? "…" + x.log.slice(Math.floor(x.log.length / 3)) : "" }; url = build(x); }
  if (url.length > maxLen && x.context?.sentence) { x = { ...x, context: { ...x.context, sentence: x.context.sentence.slice(0, 120) + "…" } }; url = build(x); }
  return url;
}

// ---- 部品を置く ----
export function installFeedback(opts) {
  addEventListener("error", (e) => rememberError(`${e.message} @ ${(e.filename || "").split("/").pop()}:${e.lineno}`));
  addEventListener("unhandledrejection", (e) => rememberError(`未処理の失敗: ${e.reason?.message || e.reason}`));
  const btn = document.createElement("button");
  btn.className = "fb-btn";
  btn.id = "fbBtn";
  btn.textContent = "!";
  btn.title = "この画面について、改善のコメントを送る";
  btn.setAttribute("aria-label", btn.title);
  btn.addEventListener("mousedown", (e) => e.preventDefault());
  btn.onclick = () => openFeedback(opts);
  document.body.appendChild(btn);
  // 開くと後ろが押せなくなるダイアログ（showModal）にも、それぞれ !○ を置く
  for (const d of document.querySelectorAll("dialog")) {
    const b2 = btn.cloneNode(true);
    b2.id = "";
    b2.classList.add("fb-in-dialog");
    b2.addEventListener("mousedown", (e) => e.preventDefault());
    b2.onclick = () => openFeedback(opts);
    d.appendChild(b2);
  }
  return btn;
}

export async function openFeedback(opts) {
  if (document.getElementById("fbDlg")) return;
  const screen = currentScreen();
  const ctx = (() => { try { return opts.getContext?.() || {}; } catch { return {}; } })();
  const btn = document.getElementById("fbBtn");
  if (btn) btn.hidden = true;
  const shot = await captureScreen({ exclude: ["#fbBtn", "#fbDlg", ".fb-in-dialog"] });
  if (btn) btn.hidden = false;
  const log = [(() => { try { return opts.getLog?.() || ""; } catch { return ""; } })(), errorLog() && `---- 画面のエラー ----\n${errorLog()}`]
    .filter(Boolean).join("\n");

  const dlg = document.createElement("div");
  dlg.id = "fbDlg";
  dlg.className = "fb-dlg";
  dlg.dataset.screen = "feedback";
  dlg.innerHTML = `
    <div class="fb-box" role="dialog" aria-label="改善のコメント">
      <div class="fb-head"><b>改善のコメント</b> <span class="fb-muted">GitHub の Issue（${opts.repo}）に送ります</span></div>
      <div class="fb-meta"><div>画面: <code class="fb-screen"></code></div><div class="fb-muted fb-ver"></div></div>
      <textarea class="fb-text" rows="5" placeholder="どこを、どう直したいか（例: 再生ボタンが小さい、ここで止まった）"></textarea>
      <label class="fb-row"><input type="checkbox" class="fb-ctx" checked> <span>開いている論文・今の文を付ける</span></label>
      <div class="fb-muted fb-ctxv"></div>
      <label class="fb-row"><input type="checkbox" class="fb-shot" checked> <span>画面の写真を付ける</span></label>
      <div class="fb-shotbox"><img class="fb-img" alt="画面の写真"><div class="fb-muted fb-noshot" hidden>このブラウザでは写真を撮れませんでした。⌘⇧4 などで撮って、ここに貼り付けられます（⌘V）。</div></div>
      <label class="fb-row"><input type="checkbox" class="fb-log" checked> <span>システムの記録の末尾を付ける（この Mac の場所は伏せる）</span></label>
      <details class="fb-logv"><summary class="fb-muted">付ける記録を見る</summary><pre></pre></details>
      <div class="fb-actions"><button class="fb-send primary">${opts.sendLabel || "GitHub に送る"}</button> <button class="fb-cancel">やめる</button> <span class="fb-msg"></span></div>
    </div>`;
  (document.querySelector("dialog[open]") || document.body).appendChild(dlg);   // 開いているダイアログの中に（後ろは押せないため）
  const $q = (s) => dlg.querySelector(s);
  $q(".fb-screen").textContent = screen;
  $q(".fb-ver").textContent = `${opts.app} ・ 版 ${opts.version}`;
  $q(".fb-ctxv").textContent = [ctx.paper, ctx.sentence && `「${ctx.sentence.slice(0, 60)}…」`].filter(Boolean).join(" ・ ") || "（なし）";
  $q(".fb-logv pre").textContent = log || "（画面側の記録は無し。Mac はサーバーの記録も付く）";
  let image = shot;
  const showImg = () => { $q(".fb-img").hidden = !image; $q(".fb-noshot").hidden = !!image; if (image) $q(".fb-img").src = image; };
  showImg();
  dlg.addEventListener("paste", (e) => {                 // 撮った写真を貼り付けて差し替え
    const f = [...(e.clipboardData?.files || [])].find((x) => x.type.startsWith("image/"));
    if (!f) return;
    e.preventDefault();
    const r = new FileReader();
    r.onload = () => { const im = new Image(); im.onload = () => { const c = document.createElement("canvas"); c.width = im.width; c.height = im.height; c.getContext("2d").drawImage(im, 0, 0); image = c.toDataURL("image/png"); showImg(); $q(".fb-shot").checked = true; }; im.src = r.result; };
    r.readAsDataURL(f);
  });
  const close = () => dlg.remove();
  $q(".fb-cancel").onclick = close;
  dlg.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } }, true);
  dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
  $q(".fb-send").onclick = async () => {
    const comment = $q(".fb-text").value.trim();
    if (!comment) { $q(".fb-msg").textContent = "コメントを書いてください"; $q(".fb-text").focus(); return; }
    const payload = { screen, comment, app: opts.app, version: opts.version, at: new Date().toISOString(),
                      context: $q(".fb-ctx").checked ? ctx : { device: ctx.device },
                      screenshot: $q(".fb-shot").checked ? image : null,
                      client_log: $q(".fb-log").checked ? log : "", include_log: $q(".fb-log").checked };
    $q(".fb-send").disabled = true;
    $q(".fb-msg").textContent = "送っています…";
    try {
      const r = await opts.send(payload);
      $q(".fb-msg").innerHTML = r?.url ? `送りました: <a target="_blank" rel="noopener"></a>` : (r?.message || "送りました");
      if (r?.url) { const a = $q(".fb-msg a"); a.href = r.url; a.textContent = r.number ? `#${r.number}` : "GitHub で開く"; }
      $q(".fb-cancel").textContent = "閉じる";
    } catch (e) {
      $q(".fb-msg").textContent = `送れませんでした: ${e.message}`;
      $q(".fb-send").disabled = false;
    }
  };
  setTimeout(() => $q(".fb-text").focus(), 0);
}
