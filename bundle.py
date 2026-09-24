"""スマホ（PWA）とのデータの受け渡し。

Mac → スマホ: paper_reader_<日時>.zip（ZIP。すべて無圧縮で格納。スマホ側は外部ライブラリなしで読む）
  manifest.json                      {"kind": "paper_reader_bundle", "version": 1, "created_at", "papers": [id...]}
  vocab.json                         単語帳（語・意味・例文）と答えの記録すべて（Vocab.export）
  papers/<id>/meta.json              題名など
  papers/<id>/sentences.json         章と文
  papers/<id>/translation.json       文ごとの日本語訳（作ってあれば）
  papers/<id>/durations.json         各文の音声の長さ（秒）。プログレスバー用
  papers/<id>/audio/<item>.m4a       1文ずつの音声（章ごとの1本ものは入れない。スマホでも1文ずつ鳴らす）

スマホ → Mac: paper_reader_progress_<日時>.json
  {"kind": "paper_reader_progress", "version": 1, "device", "exported_at", "lookups": [...], "reviews": [...]}
  Vocab.merge で合わせる（uid で重複を除く）。
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

import audio

BUNDLE_KIND = "paper_reader_bundle"
PROGRESS_KIND = "paper_reader_progress"
VERSION = 1


def make_bundle(store, vocab, paper_ids: list[str], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("paper_reader_*.zip"):
        old.unlink()                                      # 受け渡し用の置き場なので、前のものは残さない
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"paper_reader_{stamp}.zip"
    included = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as z:
        for pid in paper_ids:
            d = store.paper_dir(pid)
            paper = store.load(pid)
            z.writestr(f"papers/{pid}/meta.json", (d / "meta.json").read_text(encoding="utf-8"),
                       compress_type=zipfile.ZIP_STORED)
            z.writestr(f"papers/{pid}/sentences.json", json.dumps(paper, ensure_ascii=False),
                       compress_type=zipfile.ZIP_STORED)
            if (d / "translation.json").exists():
                z.write(d / "translation.json", f"papers/{pid}/translation.json", compress_type=zipfile.ZIP_STORED)
            st = audio.ensure_durations(d, paper)
            if st.get("durations"):
                z.writestr(f"papers/{pid}/durations.json", json.dumps(st["durations"]), compress_type=zipfile.ZIP_STORED)
            if audio.status(d).get("state") == "done":
                for it in audio.items(paper):
                    f = d / "audio" / f"{it['id']}.m4a"
                    if f.exists():
                        z.write(f, f"papers/{pid}/audio/{it['id']}.m4a", compress_type=zipfile.ZIP_STORED)
            included.append(pid)
        voc = vocab.export()
        for w in voc["words"]:                             # 例文の音声は今の文の並びから引き直す（文の番号は変わりうる）
            for e in w["examples"]:
                e["audio_ref"] = _audio_ref(store, e.get("paper_id"), e.get("sentence"))
        z.writestr("vocab.json", json.dumps(voc, ensure_ascii=False), compress_type=zipfile.ZIP_STORED)
        z.writestr("manifest.json", json.dumps({"kind": BUNDLE_KIND, "version": VERSION,
                                                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                                "papers": included}, ensure_ascii=False))
    return out


def _audio_ref(store, pid, sentence):
    if not (pid and sentence):
        return None
    try:
        paper = store.load(pid)
    except KeyError:
        return None
    for sec in paper["sections"]:
        for k, s in enumerate(sec["sentences"], 1):
            if s["t"] == sentence and s["s"]:
                return f"{pid}/{sec['id']}_{k}"
    return None


def read_progress(data: bytes) -> dict:
    j = json.loads(data.decode("utf-8"))
    if not isinstance(j, dict) or j.get("kind") != PROGRESS_KIND:
        raise ValueError("paper_reader の記録ファイルではない")
    if j.get("version") != VERSION:
        raise ValueError(f"記録ファイルの版が違う（{j.get('version')}）")
    for rv in j.get("reviews", []):
        if not (isinstance(rv.get("uid"), str) and isinstance(rv.get("headword"), str)
                and int(rv.get("rating", 0)) in (1, 2, 3, 4) and isinstance(rv.get("reviewed_at"), str)):
            raise ValueError("答えの記録の形が正しくない")
    for lk in j.get("lookups", []):
        if not (isinstance(lk.get("uid"), str) and isinstance(lk.get("headword"), str)
                and isinstance(lk.get("meaning"), str) and isinstance(lk.get("looked_at"), str)):
            raise ValueError("引いた語の記録の形が正しくない")
    return j
