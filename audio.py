"""macOS の say で読み上げ音声を作り、m4a（AAC）で保存する。iPhone・Android・どのブラウザでも鳴る。

  data/papers/<id>/audio/<sid>_h.m4a      章見出し（"Section 2. Design Drivers."）
  data/papers/<id>/audio/<sid>_<k>.m4a    章の k 文目（1始まり。引用を除いた読み上げ用の文）
  data/papers/<id>/audio/audio.json       作成の状態（state, voice, rate, total, done, durations = {音声id: 秒}, ...）
  data/papers/<id>/export/NN_<章>.m4a      スマホに持ち出す用。章（節を含む）を1本にしたもの。後付けは作らない
  data/papers/<id>/export/00_all.m4a      本文全体を1本にしたもの

作り方: say → WAV（16bit 22.05kHz mono）→ afconvert で m4a。章の1本ものは WAV を無音をはさんでつないでから変換する。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

RATE_HZ = 22050
DATA_FORMAT = f"LEI16@{RATE_HZ}"
GAP_AFTER_HEADING = 0.7
GAP_AFTER_SENTENCE = 0.35
GAP_BETWEEN_SECTIONS = 1.0
DEFAULT_VOICE = "Daniel"          # イギリス英語。最初のデモ（ブラウザ読み上げ）と同じ声で、流暢に聞こえる
BITRATE = 64000                   # 22.05kHz モノラルの AAC で上げられる上限


NOVELTY = {"Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Good News", "Jester", "Organ",
           "Superstar", "Trinoids", "Whisper", "Wobble", "Zarvox"}   # 効果音のような声は論文向きでないので出さない


def voices() -> list[dict]:
    """say で使える英語の声。"""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    vs = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2}_[A-Z]{2})\s+#", line)
        if m and m.group(2).startswith("en_") and m.group(1).strip() not in NOVELTY:
            vs.append({"name": m.group(1).strip(), "lang": m.group(2)})
    return vs


def resolve_voice(want: str | None) -> str | None:
    names = [v["name"] for v in voices()]
    for v in (want, DEFAULT_VOICE, "Samantha"):
        if v and v in names:
            return v
    return names[0] if names else None


def items(paper: dict) -> list[dict]:
    """読み上げる単位の一覧（見出し＋文）。画面の再生順と同じ。"""
    out = []
    for sec in paper["sections"]:
        out.append({"id": f"{sec['id']}_h", "sec": sec["id"], "text": sec["speech_title"], "heading": True})
        for k, s in enumerate(sec["sentences"], 1):
            if s["s"]:
                out.append({"id": f"{sec['id']}_{k}", "sec": sec["id"], "text": s["s"], "heading": False})
    return out


def items_hash(paper: dict) -> str:
    """読み上げる中身の目印。文が変わったら（AI の手直し・取り出し直し）音声を作り直すのに使う。"""
    import hashlib
    return hashlib.sha1(json.dumps([(i["id"], i["text"]) for i in items(paper)], ensure_ascii=False).encode()).hexdigest()


def is_current(paper_dir: Path, paper: dict) -> bool:
    """今ある音声が、今の文と合っているか。目印の無い古い音声は、取り出し方の版が同じなら合っているとみなして目印を書き足す。"""
    st = status(paper_dir)
    h = items_hash(paper)
    if st.get("items_hash"):
        return st["items_hash"] == h
    if st.get("state") == "done" and st.get("extractor_version") == paper.get("extractor_version") and not paper.get("manual"):
        st["items_hash"] = h
        _write_status(paper_dir, st)
        return True
    return False


def _say(text: str, wav: Path, voice: str | None, rate: int):
    cmd = ["say", "-r", str(rate), "-o", str(wav), f"--data-format={DATA_FORMAT}"]
    if voice:
        cmd[1:1] = ["-v", voice]
    subprocess.run(cmd + ["--", text], check=True, capture_output=True)


def _to_m4a(wav: Path, m4a: Path):
    tmp = m4a.with_suffix(".tmp.m4a")
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", str(BITRATE), str(wav), str(tmp)],
                   check=True, capture_output=True)
    tmp.replace(m4a)


def _concat(wavs: list[tuple[Path, float]], out: Path):
    """[(wav, 後ろに入れる無音の秒数)] をつなぐ。"""
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE_HZ)
        for p, gap in wavs:
            with wave.open(str(p), "rb") as r:
                w.writeframes(r.readframes(r.getnframes()))
            w.writeframes(b"\x00\x00" * int(RATE_HZ * gap))


def _wav_seconds(p: Path) -> float:
    with wave.open(str(p), "rb") as r:
        return round(r.getnframes() / r.getframerate(), 3)


def _m4a_seconds(p: Path) -> float:
    out = subprocess.run(["afinfo", str(p)], capture_output=True, text=True).stdout
    m = re.search(r"estimated duration:\s*([\d.]+)", out)
    return round(float(m.group(1)), 3) if m else 0.0


def ensure_durations(paper_dir: Path, paper: dict) -> dict:
    """各文の長さが audio.json に無ければ（この機能より前に作った音声）、m4a を測って書き足す。"""
    st = status(paper_dir)
    if st.get("state") != "done" or st.get("durations"):
        return st
    st["durations"] = {it["id"]: _m4a_seconds(paper_dir / "audio" / f"{it['id']}.m4a")
                       for it in items(paper) if (paper_dir / "audio" / f"{it['id']}.m4a").exists()}
    _write_status(paper_dir, st)
    return st


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:60] or "section"


def chapters(paper: dict) -> list[dict]:
    """章（level 1）ごとに、続く節をまとめる。後付けは除く。"""
    out = []
    for sec in paper["sections"]:
        if sec["kind"] == "back":
            continue
        if sec["level"] == 1 or not out:
            out.append({"title": sec["title"], "secs": [sec["id"]]})
        else:
            out[-1]["secs"].append(sec["id"])
    for i, ch in enumerate(out, 1):
        ch["file"] = f"{i:02d}_{_slug(ch['title'])}.m4a"
    return out


def status(paper_dir: Path) -> dict:
    p = paper_dir / "audio" / "audio.json"
    if not p.exists():
        return {"state": "none"}
    return json.loads(p.read_text(encoding="utf-8"))


def _write_status(paper_dir: Path, st: dict):
    p = paper_dir / "audio" / "audio.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def generate(paper_dir: Path, paper: dict, voice: str | None = None, rate: int = 175, workers: int = 4,
             on_progress=None) -> dict:
    """1文ずつの m4a と、持ち出し用の章ごとの m4a を作り直す。"""
    audio_dir, export_dir = paper_dir / "audio", paper_dir / "export"
    for d in (audio_dir, export_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    work = audio_dir / "_wav"
    work.mkdir()
    voice = resolve_voice(voice)
    its = items(paper)
    st = {"state": "running", "voice": voice, "rate": rate, "total": len(its), "done": 0,
          "extractor_version": paper.get("extractor_version"), "items_hash": items_hash(paper),
          "started_at": datetime.now().isoformat(timespec="seconds")}
    _write_status(paper_dir, st)
    try:
        def one(it):
            wav = work / f"{it['id']}.wav"
            _say(it["text"], wav, voice, rate)
            _to_m4a(wav, audio_dir / f"{it['id']}.m4a")
            return it["id"]

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for _ in ex.map(one, its):
                st["done"] += 1
                _write_status(paper_dir, st)             # 画面のプログレスバー用に1文ごとに書く
                if on_progress:
                    on_progress(st["done"], st["total"])
        st["phase"] = "chapters"                         # 1文ずつが終わり、章ごとの1本ものをつなぐ段階
        _write_status(paper_dir, st)

        st["durations"] = {it["id"]: _wav_seconds(work / f"{it['id']}.wav") for it in its}   # プログレスバー用
        by_sec: dict[str, list] = {}
        for it in its:
            gap = GAP_AFTER_HEADING if it["heading"] else GAP_AFTER_SENTENCE
            by_sec.setdefault(it["sec"], []).append((work / f"{it['id']}.wav", gap))
        chs = chapters(paper)
        everything = []
        for ch in chs:
            parts = [x for sid in ch["secs"] for x in by_sec.get(sid, [])]
            if not parts:
                continue
            parts[-1] = (parts[-1][0], GAP_BETWEEN_SECTIONS)
            everything += parts
            tmp = work / "_chapter.wav"
            _concat(parts, tmp)
            _to_m4a(tmp, export_dir / ch["file"])
        if everything:
            tmp = work / "_all.wav"
            _concat(everything, tmp)
            _to_m4a(tmp, export_dir / "00_all.m4a")
        st.update(state="done", chapters=[{"title": c["title"], "file": c["file"]} for c in chs],
                  finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # say / afconvert の失敗など
        st.update(state="error", error=str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _write_status(paper_dir, st)
    return st


def word_audio(words_dir: Path, word: str, voice: str | None, rate: int) -> Path | None:
    """単語の発音（data/words/<声>/<word>.m4a）。無ければ作る。声を変えたら別に作る。"""
    w = word.lower()
    if not re.fullmatch(r"[a-z][a-z\-']{0,40}", w):
        return None
    v = resolve_voice(voice)
    words_dir = words_dir / re.sub(r"[^A-Za-z0-9]+", "_", v or "default")
    words_dir.mkdir(parents=True, exist_ok=True)
    m4a = words_dir / f"{w}.m4a"
    if not m4a.exists():
        wav = words_dir / f"{w}.wav"
        _say(word, wav, v, rate)
        _to_m4a(wav, m4a)
        wav.unlink(missing_ok=True)
    return m4a
