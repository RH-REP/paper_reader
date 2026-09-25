"""論文の図・表・数式を画像にする。

  data/papers/<id>/figures/<file>.png
  data/papers/<id>/figures.json
    {"version": 1, "items": [{"file", "kind": "figure"|"table"|"equation"|"image", "label": "Fig. 2",
                              "caption", "page", "by": "auto"|"ai"}],
     "manual": {...}（AI が手直ししたら付く。付いていれば自動の抜き出しで上書きしない）}

自動の抜き出し（版 2）: キャプション（「Fig. 2.」「FIGURE 2 |」「Table 1.」）から探し、その上（表は下）の次の本文の段落までにある
画像・線・小さい字をまとめて1枚に切り抜く（線で描いたグラフ、分かれた画像も1枚になる）。罫線の表は表の枠。
番号付きの独立した行の数式（右端に「(3)」）も切り抜く。直しきれないものは AI の手直し（ai_fix_prompt.md）に任せる。
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

VERSION = 2                      # 2: キャプションから探す（線で描いた図・表・数式、分かれた画像をまとめる）
DPI = 200
MIN_PT = 60                      # これより小さい画像（ロゴ・アイコン）は外す（ポイント単位の幅・高さ）
LOOSE_PT = 120                   # キャプションの無い画像は、これより大きいものだけ残す
PAD = 3
KINDS = ("figure", "table", "equation", "image")
CAPTION = re.compile(r"^(fig(?:ure)?s?\.?|table|tab\.|eq(?:uation)?s?\.?)\s*\(?(\d+[a-z]?)\)?\s*[.:|]?\s*", re.I)
# キャプションの頭: 「Fig. 2.」「Figure 2:」「FIGURE 2 |」「Table 1.」（「Figure 2 shows …」のような本文は外す）
CAPTION_HEAD = re.compile(r"^(Fig(?:ure)?\.?|FIG(?:URE)?\.?|Table|TABLE|Tab\.)\s*([A-Z]?-?\d+[a-z]?)\s*(?:[.:|]|\s*$)")
EQ_NUM = re.compile(r"\(\s*(\d{1,3})\s*\)\s*$")
MATH_FONT = re.compile(r"math|cmmi|cmsy|cmex|standardsym|mtextra|symbol|euclid|stix|cambria", re.I)


def _kind(word: str) -> str:
    w = word.lower()
    return "table" if w.startswith("tab") else "equation" if w.startswith("eq") else "figure"


def _label(kind: str, num: str) -> str:
    return {"figure": f"Fig. {num}", "table": f"Table {num}", "equation": f"Eq. ({num})"}[kind]


def status(paper_dir: Path) -> dict:
    p = paper_dir / "figures.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"version": 0, "items": []}


def _body_size(doc) -> float:
    sizes = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for sp in l["spans"]:
                    sizes[round(sp["size"], 1)] += len(sp["text"].strip())
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _blocks(page, body: float) -> list[dict]:
    """文字の塊。種類: caption（キャプション）/ para（本文の段落）/ small（図の中の小さい字など）/ other"""
    out = []
    width = page.rect.width
    for b in page.get_text("dict")["blocks"]:
        lines = [l for l in b.get("lines", []) if "".join(sp["text"] for sp in l["spans"]).strip()]
        if not lines:
            continue
        text = unicodedata.normalize("NFKC", " ".join(" ".join(sp["text"] for sp in l["spans"]) for l in lines))
        text = " ".join(text.split())
        sizes = Counter()
        for l in lines:
            for sp in l["spans"]:
                sizes[round(sp["size"], 1)] += len(sp["text"].strip())
        size = sizes.most_common(1)[0][0]
        r = pymupdf.Rect(b["bbox"])
        avg_w = sum(pymupdf.Rect(l["bbox"]).width for l in lines) / len(lines)
        kind = "other"
        if CAPTION_HEAD.match(text):
            kind = "caption"
        elif size >= body - 0.3 and len(text) >= 40 and (len(lines) >= 2 and avg_w >= width * 0.3 or avg_w >= width * 0.55):
            kind = "para"
        elif size < body - 0.5:
            kind = "small"
        out.append({"rect": r, "text": text, "size": size, "kind": kind, "lines": lines})
    return out


def _graphics(page) -> list:
    """画像と線（描画）の範囲。ページ全体の背景・ページをまたぐ細い罫線は外す。"""
    area = page.rect.width * page.rect.height
    out = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if not r.is_empty and r.width * r.height < area * 0.8:
            out.append(r)
    try:
        drawings = page.get_drawings()
    except Exception:  # 壊れた描画
        drawings = []
    for d in drawings:
        r = pymupdf.Rect(d["rect"]) & page.rect
        if r.is_empty and not (r.width or r.height):
            continue
        edge = r.y1 < page.rect.height * 0.1 or r.y0 > page.rect.height * 0.9     # ヘッダー・フッターの罫線
        if r.width * r.height > area * 0.8 or (r.height < 1.5 and r.width > page.rect.width * 0.7 and edge):
            continue
        out.append(r)
    return out


def _overlap_x(a, b, slack=0.0) -> bool:
    return a.x0 - slack < b.x1 and b.x0 - slack < a.x1


def _region(page, cap: dict, blocks: list[dict], gfx: list, tables: list, direction: str):
    """キャプションの上（direction="up"）か下（"down"）で、次の本文の段落までにある画像・線・小さい字をまとめた範囲。"""
    c = cap["rect"]
    top, bottom = page.rect.y0 + 20, page.rect.y1 - 20
    for b in blocks:
        if b is cap or b["kind"] not in ("para", "caption") or not _overlap_x(b["rect"], c):
            continue
        if direction == "up" and b["rect"].y1 <= c.y0 + 1:
            top = max(top, b["rect"].y1)
        if direction == "down" and b["rect"].y0 >= c.y1 - 1:
            bottom = min(bottom, b["rect"].y0)
    lo, hi = (top, c.y0 + 2) if direction == "up" else (c.y1 - 2, bottom)
    band = pymupdf.Rect(min(c.x0, page.rect.x0 + 30) if c.width > page.rect.width * 0.6 else c.x0 - 25, lo,
                        max(c.x1, page.rect.x1 - 30) if c.width > page.rect.width * 0.6 else c.x1 + 25, hi)
    for t in tables:                                # 罫線の表は表の枠をそのまま使う
        if band.intersects(t) and (t & band).height > t.height * 0.6:
            return t
    # キャプションが図の下の端に少し重なることがある（Stahl 2020）。中心がキャプションのこちら側にあればよい
    if direction == "up":
        parts = [g for g in gfx if g.y0 >= lo - 2 and g.y0 < c.y0 and (g.y0 + g.y1) / 2 <= c.y1 and _overlap_x(g, band)]
    else:
        parts = [g for g in gfx if g.y1 <= hi + 2 and g.y1 > c.y1 and (g.y0 + g.y1) / 2 >= c.y0 and _overlap_x(g, band)]
    if not parts or max(p.width * p.height for p in parts) < 400:
        return None
    r = pymupdf.Rect(parts[0])
    for p in parts[1:]:
        r |= p
    for b in blocks:                                # 図の中の字（軸の目盛り・ラベル）も入れる
        if b["kind"] in ("small", "other") and b["rect"].y0 >= lo - 2 and b["rect"].y1 <= hi + 2 and _overlap_x(b["rect"], r, 10):
            r |= b["rect"]
    return r


def _eq_text(l) -> str:
    t = unicodedata.normalize("NFKC", "".join(sp["text"] for sp in l["spans"])).strip()
    return t.replace("ð", "(").replace("Þ", ")").replace("þ", ")")   # Applied Optics の数式のフォント


def _mathy(l) -> bool:
    t = "".join(sp["text"] for sp in l["spans"])
    plain = " ".join(sp["text"] for sp in l["spans"] if not MATH_FONT.search(sp["font"]))
    if "=" not in t and "1⁄4" not in t and len(re.findall(r"\b[a-z]{3,}\b", plain)) >= 5:
        return False                                   # 「where σ represents the error …」のような文の行は式にしない
    n_math = sum(len(sp["text"]) for sp in l["spans"] if MATH_FONT.search(sp["font"]))
    return "=" in t or "1⁄4" in t or n_math >= 0.3 * max(1, len(t.strip())) or bool(re.search(r"[\u0700-\u0DFFðÞ∑∫√]", t))


def _rule_table(page, cap: dict, gfx: list):
    """横罫だけの表（縦の罫線が無いので find_tables で見つからない）: キャプションの下に並ぶ横罫の、最初から最後まで。"""
    c = cap["rect"]
    rules = sorted((g for g in gfx if g.height < 2.5 and g.width > 60 and g.y0 > c.y1 - 2 and g.y0 < c.y1 + page.rect.height * 0.7
                    and _overlap_x(g, c, 30)), key=lambda g: g.y0)
    if len(rules) < 2:
        return None
    group = [rules[0]]
    for g in rules[1:]:
        if g.y0 - group[-1].y0 > 260:
            break
        group.append(g)
    if len(group) < 2 or group[0].y0 - c.y1 > 40:
        return None
    r = pymupdf.Rect(group[0])
    for g in group[1:]:
        r |= g
    return r


def _equations(page, blocks: list[dict], body: float) -> list[tuple[str, pymupdf.Rect, str]]:
    """番号付きの独立した行の数式。右端の「(3)」（同じ行の式の続きか、番号だけの行）と、同じ高さの式の行をまとめる。"""
    out = []
    width = page.rect.width
    lines = [(b, l) for b in blocks if b["kind"] != "caption" for l in b["lines"]]
    def is_prose(l) -> bool:                       # 文の行（ふつうの語が5つ以上で、= を含まない）
        t = "".join(sp["text"] for sp in l["spans"])
        plain = " ".join(sp["text"] for sp in l["spans"] if not MATH_FONT.search(sp["font"]))
        return "=" not in t and "1⁄4" not in t and len(re.findall(r"\b[a-z]{3,}\b", plain)) >= 5

    prose = [pymupdf.Rect(l["bbox"]) for _, l in lines if is_prose(l)]

    def in_prose_row(r) -> bool:
        return any(min(r.y1, p.y1) - max(r.y0, p.y0) > 0.5 * min(r.height, p.height)
                   and p.x0 < r.x1 + 30 and r.x0 < p.x1 + 30 for p in prose)     # 同じ段の同じ高さに文がある

    for b, l in lines:
        t = _eq_text(l)
        m = EQ_NUM.search(t)
        lr = pymupdf.Rect(l["bbox"])
        if not m or lr.x1 < width * 0.6 or len(t) > 160 or any(re.search(r"arial|helvetica|sans", sp["font"], re.I) for sp in l["spans"]):
            continue
        only_num = t == m.group(0).strip()
        r = pymupdf.Rect(lr)
        found = not only_num and _mathy(l)
        for b2, o in lines:                            # 同じ高さ（と上下に続く分数など）の式の行
            orr = pymupdf.Rect(o["bbox"])
            if o is l or orr.x1 > lr.x0 + 2 and only_num or orr.width > width * 0.75:
                continue
            if orr.y1 > lr.y0 - body * 1.6 and orr.y0 < lr.y1 + body * 1.6 and _mathy(o) and not in_prose_row(orr):
                r |= orr
                found = True
        if found:                                      # 添字・分数の下の段が切れないよう上下に少し足す
            out.append((m.group(1), pymupdf.Rect(r.x0, r.y0 - body * 0.3, r.x1, r.y1 + body * 0.45), t))
    return out


def extract(paper_dir: Path, force: bool = False) -> dict:
    """キャプションから図・表を探して切り抜き、番号付きの数式も切り抜く。手直し済み（manual）は触らない。
      図: キャプションの上（無ければ下）の、次の本文の段落までにある画像・線・小さい字をまとめた範囲
      表: 罫線の表（find_tables）の枠。無ければ図と同じ探し方（下を先に）
      式: 右端に「(n)」がある、数式のフォントか「=」を含む行
      キャプションに結び付かない 120pt 以上の画像は、種類の分からない画像（image）として残す"""
    cur = status(paper_dir)
    if cur.get("manual") or (cur.get("version") == VERSION and not force):
        return cur
    out = paper_dir / "figures"
    out.mkdir(exist_ok=True)
    for old in out.glob("auto_*.png"):
        old.unlink()
    items, used_labels = [], set()
    with pymupdf.open(paper_dir / "original.pdf") as doc:
        body = _body_size(doc)
        for pno, page in enumerate(doc):
            blocks = _blocks(page, body)
            gfx = _graphics(page)
            try:
                tables = [pymupdf.Rect(t.bbox) for t in page.find_tables().tables if t.row_count >= 2 and t.col_count >= 2]
            except Exception:
                tables = []
            taken = []
            for cap in (b for b in blocks if b["kind"] == "caption"):
                m = CAPTION_HEAD.match(cap["text"])
                kind = _kind(m.group(1))
                label = _label(kind, m.group(2))
                if label in used_labels:
                    continue                          # 同じ番号の2つ目（本文の中の言及など）は使わない
                order = ("down", "up") if kind == "table" else ("up", "down")
                if kind == "table":                   # 表の中身は本文と同じ大きさの字なので、先に表の枠・横罫で探す
                    tb = next((t for t in tables if _overlap_x(t, cap["rect"]) and -40 < t.y0 - cap["rect"].y1 < 60), None)
                    r = tb or _rule_table(page, cap, gfx)
                    if r is not None:
                        used_labels.add(label)
                        taken.append(r)
                        items.append({"kind": kind, "label": label, "caption": cap["text"][:900], "page": pno + 1,
                                      "by": "auto", "_rect": (pno, r)})
                        continue
                r = _region(page, cap, blocks, gfx, tables, order[0]) or _region(page, cap, blocks, gfx, tables, order[1])
                if r is None and kind == "table":
                    r = _rule_table(page, cap, gfx)
                if r is None or r.width < MIN_PT / 2 or r.height < 20:
                    continue
                used_labels.add(label)
                taken.append(r)
                items.append({"kind": kind, "label": label, "caption": cap["text"][:900], "page": pno + 1, "by": "auto",
                              "_rect": (pno, r)})
            for num, r, t in _equations(page, blocks, body):
                label = _label("equation", num)
                if label in used_labels:
                    continue
                used_labels.add(label)
                items.append({"kind": "equation", "label": label, "caption": t[:300], "page": pno + 1, "by": "auto",
                              "_rect": (pno, r)})
            for info in page.get_image_info():         # キャプションの無い大きい画像
                r = pymupdf.Rect(info["bbox"]) & page.rect
                if r.is_empty or r.width < LOOSE_PT or r.height < LOOSE_PT or any((r & t).get_area() > r.get_area() * 0.5 for t in taken):
                    continue
                taken.append(r)
                items.append({"kind": "image", "label": "", "caption": "", "page": pno + 1, "by": "auto", "_rect": (pno, r)})
        items.sort(key=lambda it: (it["page"], it["_rect"][1].y0))
        counters = Counter()
        for it in items:
            pno, r = it.pop("_rect")
            short = {"figure": "fig", "table": "tab", "equation": "eq", "image": "img"}[it["kind"]]
            counters[short] += 1
            it["file"] = f"auto_{short}{counters[short]:02d}.png"
            clip = pymupdf.Rect(r.x0 - PAD, r.y0 - PAD, r.x1 + PAD, r.y1 + PAD) & doc[pno].rect
            doc[pno].get_pixmap(clip=clip, dpi=DPI).save(out / it["file"])
    st = {"version": VERSION, "items": [{k: it[k] for k in ("file", "kind", "label", "caption", "page", "by")} for it in items]}
    tmp = paper_dir / "figures.json.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(paper_dir / "figures.json")
    return st


def check(paper_dir: Path) -> tuple[list[str], list[str]]:
    """figures.json の形と画像の有無を確かめる（check_paper.py から使う）。(エラー, 警告)"""
    p = paper_dir / "figures.json"
    if not p.exists():
        return [], ["figures.json が無い（図・表・数式が無い論文ならよい）"]
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        return [f"figures.json が JSON として読めない: {e}"], []
    errs, warns = [], []
    if not isinstance(st.get("items"), list):
        return ["figures.json に items（配列）が無い"], []
    for i, it in enumerate(st["items"]):
        where = f"figures.json items[{i}]"
        f = it.get("file") if isinstance(it, dict) else None
        if not isinstance(f, str) or not re.fullmatch(r"[A-Za-z0-9_\-]+\.png", f):
            errs.append(f"{where}.file は英数字・_・- の .png 名")
            continue
        if not (paper_dir / "figures" / f).exists():
            errs.append(f"{where}: figures/{f} が無い")
        if it.get("kind") not in KINDS:
            errs.append(f"{where}.kind は {' / '.join(KINDS)} のどれか")
        if it.get("kind") in ("figure", "table", "equation") and not it.get("label"):
            warns.append(f"{where}: label（Fig. 2 など）が無い")
        if it.get("kind") == "image":
            warns.append(f"{where}: 種類が分からない画像（{f}）。図・表・数式なら kind と label を付け、ロゴなどなら消す")
    return errs, warns
