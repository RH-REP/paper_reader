"""論文の図・表・数式を画像にする。

  data/papers/<id>/figures/<file>.png
  data/papers/<id>/figures.json
    {"version": 1, "items": [{"file", "kind": "figure"|"table"|"equation"|"image", "label": "Fig. 2",
                              "caption", "page", "by": "auto"|"ai"}],
     "manual": {...}（AI が手直ししたら付く。付いていれば自動の抜き出しで上書きしない）}

自動の抜き出し: PDF に埋め込まれた画像の範囲を、ページから高解像度で切り抜く（ラベルなど上に重なった文字も入る）。
近くに「Fig. 2」「FIGURE 2 |」「Table 1」「Eq. (3)」で始まる行があれば、種類・番号・キャプションを付ける。
線で描かれたグラフ・表・数式の切り抜きや、番号の直しは AI の手直し（ai_fix_prompt.md）に任せる。
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pymupdf

VERSION = 1
DPI = 200
MIN_PT = 60                      # これより小さい画像（ロゴ・アイコン）は外す（ポイント単位の幅・高さ）
CAPTION_GAP = 80                 # 画像の上下この距離（pt）以内のキャプションを探す
KINDS = ("figure", "table", "equation", "image")
CAPTION = re.compile(r"^(fig(?:ure)?s?\.?|table|tab\.|eq(?:uation)?s?\.?)\s*\(?(\d+[a-z]?)\)?\s*[.:|]?\s*", re.I)


def _kind(word: str) -> str:
    w = word.lower()
    return "table" if w.startswith("tab") else "equation" if w.startswith("eq") else "figure"


def _label(kind: str, num: str) -> str:
    return {"figure": f"Fig. {num}", "table": f"Table {num}", "equation": f"Eq. ({num})"}[kind]


def _caption_near(page, bbox) -> tuple[str, str, str] | None:
    """画像の下（なければ上）にあるキャプションの段落を探す。(kind, label, caption)"""
    best = None
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
        text = unicodedata.normalize("NFKC", " ".join(text.split()))
        m = CAPTION.match(text)
        if not m or x1 < bbox.x0 - 20 or x0 > bbox.x1 + 20:
            continue
        below = y0 - bbox.y1
        above = bbox.y0 - y1
        dist = below if 0 <= below <= CAPTION_GAP else above + 1000 if 0 <= above <= CAPTION_GAP else None
        if dist is not None and (best is None or dist < best[0]):
            kind = _kind(m.group(1))
            best = (dist, kind, _label(kind, m.group(2)), text[:600])
    return best[1:] if best else None


def status(paper_dir: Path) -> dict:
    p = paper_dir / "figures.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"version": 0, "items": []}


def extract(paper_dir: Path, force: bool = False) -> dict:
    """埋め込み画像を切り抜いて figures/ と figures.json を作る。手直し済み（manual）は触らない。"""
    cur = status(paper_dir)
    if cur.get("manual") or (cur.get("version") == VERSION and not force):
        return cur
    out = paper_dir / "figures"
    out.mkdir(exist_ok=True)
    for old in out.glob("auto_*.png"):
        old.unlink()
    items, seen = [], set()
    with pymupdf.open(paper_dir / "original.pdf") as doc:
        for pno, page in enumerate(doc):
            k = 0
            for info in page.get_image_info(xrefs=True):
                bbox = pymupdf.Rect(info["bbox"]) & page.rect
                key = (pno, round(bbox.x0), round(bbox.y0), round(bbox.x1), round(bbox.y1))
                if bbox.is_empty or bbox.width < MIN_PT or bbox.height < MIN_PT or key in seen:
                    continue
                seen.add(key)
                k += 1
                name = f"auto_p{pno + 1:02d}_{k}.png"
                page.get_pixmap(clip=bbox, dpi=DPI).save(out / name)
                cap = _caption_near(page, bbox)
                kind, label, caption = cap if cap else ("image", "", "")
                items.append({"file": name, "kind": kind, "label": label, "caption": caption,
                              "page": pno + 1, "by": "auto"})
    st = {"version": VERSION, "items": items}
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
