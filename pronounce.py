"""読み上げる前の読み方の直し。音声（say）に渡す文だけに使い、画面の文・訳・辞書には使わない。

  組み込み（いつも使う）: 数字のあとの単位（7 μm → 7 micrometers）、上付き（cm⁻² → centimeters to the minus 2）、
                          ギリシャ文字（λ → lambda）、× → times など。say がそのままでは読み違えるもの
  自分の辞書: <data_root>/pronunciations.json  {"rules": [{"from": "DM", "to": "D M", "case": true}]}
              語全体に合うときだけ置き換える。case が true なら大文字小文字も合わせる（略語向け）

読み方を変えると、その語を含む文だけ音声が作り直される（audio.py の控えは読む文の中身で引くため）。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
UNITS = {"μm": "micrometers", "µm": "micrometers", "nm": "nanometers", "mm": "millimeters", "cm": "centimeters",
         "km": "kilometers", "μs": "microseconds", "µs": "microseconds", "ms": "milliseconds", "ns": "nanoseconds",
         "Hz": "hertz", "kHz": "kilohertz", "MHz": "megahertz", "GHz": "gigahertz", "kV": "kilovolts", "mV": "millivolts",
         "V": "volts", "mA": "milliamps", "W": "watts", "mW": "milliwatts", "kW": "kilowatts", "K": "kelvin",
         "°C": "degrees Celsius", "°": "degrees", "Pa": "pascals", "kPa": "kilopascals", "MPa": "megapascals",
         "GPa": "gigapascals", "N": "newtons", "J": "joules", "mJ": "millijoules", "s": "seconds", "h": "hours", "min": "minutes", "mN": "millinewtons", "kg": "kilograms", "g": "grams", "Gy": "gray",
         "krad": "kilorads", "rad": "radians", "mrad": "milliradians", "μrad": "microradians", "arcsec": "arcseconds",
         "arcmin": "arcminutes", "ppm": "parts per million"}
GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "Δ": "delta", "∆": "delta", "ε": "epsilon", "θ": "theta",
         "λ": "lambda", "μ": "mu", "µ": "mu", "ν": "nu", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi",
         "ω": "omega", "Ω": "ohms"}
POWER_WORDS = {"2": "squared", "3": "cubed"}


def _power(m: re.Match) -> str:
    base, exp = m.group(1), m.group(2).translate(SUP)
    if exp.startswith("-"):
        return f"{base} to the minus {exp[1:]}"
    return f"{base} {POWER_WORDS[exp]}" if exp in POWER_WORDS else f"{base} to the {exp}"


_LONG = sorted((re.escape(u) for u in UNITS if len(u) > 1), key=len, reverse=True)
_SHORT = [re.escape(u) for u in UNITS if len(u) == 1 and u != "°"]
# 2字以上の単位は数字に続けて書いてもよい（7μm）。1字の単位は空白が要る（1990s を「1990 seconds」にしない）
_UNIT_RE = re.compile(r"(?<=\d)(?:\s?(" + "|".join(_LONG + ["°"]) + r")|\s(" + "|".join(_SHORT) + r"))(?![A-Za-z0-9-])")
_POW_RE = re.compile(r"([A-Za-z0-9)]+)([⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
_UNIT_IN_POW = re.compile(r"\b(" + "|".join(sorted(map(re.escape, (u for u in UNITS if u.isascii())), key=len, reverse=True))
                          + r") (squared|cubed|to the minus \d+)")


def builtin(text: str) -> str:
    """組み込みの読み方の直し。"""
    t = re.sub(r"(\d)\s?×\s?10([⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)", lambda m: f"{m.group(1)} times 10 to the "
               + ("minus " + m.group(2).translate(SUP)[1:] if m.group(2).startswith("⁻") else m.group(2).translate(SUP)), text)
    t = re.sub(r"(?<=[A-Za-z²³])/(cm|mm|m|s|h|K|Hz|W|J|kg|g|min)(?=[²³⁻\s.,;:)]|$)", r" per \1", t)   # J/cm² → J per cm²
    t = re.sub(r"\bper (cm|mm|m)²", lambda m: f"per square {UNITS.get(m.group(1), 'meters')[:-1]}", t)
    t = re.sub(r"(?<=[A-Za-zα-ω])\s?/\s?(\d+)\b", r" over \1", t)                    # λ/14 → λ over 14
    t = _POW_RE.sub(_power, t)                                      # cm⁻² → cm to the minus 2
    t = _UNIT_IN_POW.sub(lambda m: f"{UNITS[m.group(1)] if m.group(1) in UNITS else m.group(1)} {m.group(2)}", t)
    t = _UNIT_RE.sub(lambda m: " " + UNITS[m.group(1) or m.group(2)], t)   # 7 μm → 7 micrometers
    t = re.sub(r"\bper (s|h|min|K)\b", lambda m: "per " + {"s": "second", "h": "hour", "min": "minute", "K": "kelvin"}[m.group(1)], t)
    t = re.sub(r"\s?×\s?", " times ", t)
    t = re.sub(r"±\s?(?=\d)", " plus or minus ", t)
    t = re.sub(r"[" + "".join(GREEK) + r"]", lambda m: f" {GREEK[m.group(0)]} ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


class Pronouncer:
    """自分の辞書（pronunciations.json）と組み込みの直しをまとめて使う。"""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._mtime = None
        self._rules: list[dict] = []
        self._re = None

    def rules(self) -> list[dict]:
        self._load()
        return [dict(r) for r in self._rules]

    def _load(self):
        if not self.path:
            return
        with self._lock:
            mt = self.path.stat().st_mtime if self.path.exists() else None
            if mt == self._mtime:
                return
            self._mtime = mt
            raw = json.loads(self.path.read_text(encoding="utf-8")) if mt else {}
            self._rules = [r for r in raw.get("rules", []) if isinstance(r, dict) and str(r.get("from", "")).strip()]
            self._compile()

    def _compile(self):
        self._re = []
        for r in sorted(self._rules, key=lambda r: -len(r["from"])):   # 長い語から先に（"DM" より "DMs"）
            flags = 0 if r.get("case") else re.I
            self._re.append((re.compile(r"(?<![\w-])" + re.escape(r["from"].strip()) + r"(?![\w-])", flags), r["to"]))

    def save(self, rules: list[dict]):
        clean, seen = [], set()
        for r in rules:
            f, to = str(r.get("from", "")).strip(), str(r.get("to", "")).strip()
            if not f or not to or len(f) > 60 or len(to) > 120 or f in seen:
                continue
            seen.add(f)
            clean.append({"from": f, "to": to, "case": bool(r.get("case", any(c.isupper() for c in f)))})
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": clean}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        self._mtime = None
        return clean

    def apply(self, text: str) -> str:
        self._load()
        for rx, to in self._re or []:
            text = rx.sub(to, text)
        return builtin(text)
