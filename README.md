# paper_reader

英語論文の PDF を**章ごと**に分けて読み上げ、文中で引いた英単語を**単語帳**に貯めて **FSRS**（Anki と同じ方式）で復習するアプリ。

- **Mac 版**（このリポジトリの直下）: PDF の取り込み・章と文への分割・OCR・読み上げ音声づくり（macOS の `say`）・辞書・単語帳・復習。ブラウザで使うローカルサーバー。
- **スマホ版**（`pwa/`）: Android の Chrome で動く PWA。Mac で作った論文・音声・単語帳を読み込み、オフラインで聞く・引く・復習する。
  → **https://rh-rep.github.io/paper_reader/** （GitHub Pages。使い方は app の「使い方」タブ）

論文・音声・単語帳・復習の記録は、使う人の Mac とスマホの中にだけ置きます。このリポジトリにもサーバーにも送りません。

## Mac 版

| ファイル | 役目 |
|---|---|
| `extract.py` | PDF → 題名・章・文。文字層が無いページは Tesseract で OCR。見出しはフォント（太字・本文より大きい）で見分け、OCR ページは「1 INTRODUCTION」型の番号で見分ける |
| `store.py` | `data/papers/<sha8>/` への取り込み（元の PDF はコピーするだけ）・一覧・読み出し |
| `audio.py` | `say` → WAV → `afconvert` で m4a（AAC）。1文ずつと、持ち出し用の章ごと・全体の1本もの |
| `lookup.py` | 単語を引く。EJDict → 活用を戻す → 派生語を戻す（近い語）→ Free Dictionary API（英英、ネット） |
| `vocab.py` | 単語帳（`vocab.sqlite`）。辞書で引いて「登録」した語を入れる。答えの記録（reviews）が復習の正本 |
| `srs.py` | 復習の計算（py-fsrs、FSRS-6 の既定値、fuzz なし）。新しい語は1日20語まで |
| `bundle.py` | スマホへ渡す zip と、スマホから戻る記録（JSON）の形 |
| `share.py` | 同じ Wi‑Fi のスマホへの一時的な受け渡しページ（合言葉付きのアドレス、10分で閉じる） |
| `server.py` | 画面と API を 127.0.0.1 で配る（標準ライブラリの `http.server`） |
| `index.html` `assets/` | 画面（読む・復習・スマホ） |
| `tools/` | 取り込み（`import_pdf.py`）、辞書づくり（`build_dict.py` `build_pwa_dict.py`）、架空のサンプル PDF（`make_sample_pdf.py`） |

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.json config.json
.venv/bin/python tools/build_dict.py --config config.json      # 英和辞書（EJDict-hand、約4.4MB）を取ってくる
.venv/bin/python server.py --config config.json                # http://127.0.0.1:8796/
```

- 必要なもの: macOS（`say` と `afconvert`）、Python 3.11 以上。OCR を使うなら Tesseract（`brew install tesseract`）。
- データは `config.json` の `data_root`（既定 `data/`）に置く。

## スマホ版（`pwa/`）

| ファイル | 役目 |
|---|---|
| `index.html` `app.css` `js/app.js` | 画面（読む・復習・単語帳・データ・使い方） |
| `js/srs.js` | 復習の計算。py-fsrs の `review_card` を写したもの（Mac と同じ記録から同じ結果になる。`tests/test_pwa_parity.py`） |
| `js/lookup.js` | 単語を引く（Mac 版と同じ順）。辞書は `dict/<頭文字>.json` |
| `js/db.js` | IndexedDB への保存 |
| `js/zip.js` | Mac から来る zip（無圧縮）を読む |
| `sw.js` `manifest.webmanifest` `icons/` | オフライン動作とホーム画面への追加 |

`pwa/dict/` は GitHub Actions（`.github/workflows/pages.yml`）が EJDict から作って Pages に載せます（リポジトリには入れない）。

### Mac とスマホの受け渡し

- **Mac → スマホ**: Mac の「スマホ」で論文を選び「Wi‑Fi でスマホに送る」→ スマホで QR を読んで zip を保存 → PWA の「データ」で読み込む。「ファイルで渡す」で zip を保存して Google Drive などで移してもよい。
- **スマホ → Mac**: PWA の「データ」→「記録を書き出す」→ 受け渡しページの「記録を Mac に送る」か、Mac の「記録ファイルを読み込む」。
- 復習の状態は保存せず、両方の答えの記録（uid 付き）を時刻順に計算し直して作る。同じ記録を何度読み込んでも二重にならない。

## テスト

```sh
.venv/bin/python -m unittest discover -s tests -v
```

架空のサンプル PDF（`samples/`、`tools/make_sample_pdf.py` で作る）と、テストの中で作る小さな架空の辞書を使います。

## ライセンス

- このリポジトリ: AGPL-3.0（PyMuPDF が AGPL のため）
- 英和辞書 EJDict-hand: パブリックドメイン（CC0） https://github.com/kujirahand/EJDict
- `pwa/js/srs.js` は py-fsrs（MIT、Copyright (c) 2022 Open Spaced Repetition）の計算を写したもの
