# paper_reader

英語論文の PDF を**章ごと**に分けて読み上げ、文中で引いた英単語を**単語帳**に貯めて **FSRS**（Anki と同じ方式）で復習するアプリ。

- **Mac 版**（このリポジトリの直下）: PDF の取り込み・貼り付けた英文の取り込み・章と文への分割・OCR・読み上げ音声づくり（macOS の `say`）・章の編集・辞書・単語帳・復習。
  しおり（続きから）・文の印（★）と Markdown への書き出し・本文の Fig. から図を開く・読み方の辞書・専門用語の一覧・取り出しの点検と AI（Claude Code）への手直しの依頼。ブラウザで使うローカルサーバー。
- **スマホ版**（`pwa/`）: Android の Chrome で動く PWA。Mac で作った論文・音声・単語帳を読み込み、オフラインで聞く・引く・復習する。
  → **https://rh-rep.github.io/paper_reader/** （GitHub Pages。使い方は app の「使い方」タブ）

論文・音声・単語帳・復習の記録は、使う人の Mac とスマホの中にだけ置きます。このリポジトリにもサーバーにも送りません。

## Mac 版

| ファイル | 役目 |
|---|---|
| `extract.py` | PDF → 題名・章・文（取り出し方 v3: 1段組みの判定、短冊に分けて描かれた行、斜体の小節、要旨の前の参考文献、Symbol の私用領域の文字、複合語のハイフン、上付き、参考文献は1件1文）。文字層が無いページは Tesseract で OCR。見出しはフォント（太字・本文より大きい）で見分け、OCR ページは「1 INTRODUCTION」型の番号で見分ける。貼り付けた英文は `build_from_text`（「## 見出し」「1. Introduction」、前が空行の短い行を見出しにする） |
| `store.py` | `data/papers/<sha8>/` への取り込み（元の PDF はコピーするだけ。貼り付けた英文は `original.txt`）・一覧・読み出し・章の編集（`edit_sections`: 段の上げ下げはその章と下の節だけ、分ける、前とつなぐ、名前。編集すると `manual` が付く） |
| `figures.py` | 図・表・数式の画像（版 2）。キャプション（Fig. / FIGURE / Table）から探し、その上（表は下）の画像・線・小さい字をまとめて 200dpi で切り抜く。罫線・横罫の表、右端に番号のある式も切り抜く。直しきれないものは AI の手直しで（`figures.json` の `manual`） |
| `quality.py` | 取り出した章と文の点検（章の並び・番号の飛び・文字の重なり・見えない文字・要旨に紛れた文献など）。重大なものがあれば画面で AI の手直しを勧める。`tools/check_paper.py` でも使う |
| `aifix.py` | 画面のボタンで、この Mac の Claude Code（`claude -p`）に手直しを頼む。その論文のフォルダの読み書きと app の Python だけを許し、pip・ネットは禁止。対話用の CLAUDE.md は読ませない（`--setting-sources local`）。1本ずつ |
| `state.py` | しおり（文の中身で覚える）と文の印・メモ（`state.sqlite`）。Mac とスマホで更新日時の新しいほうに合わせる |
| `pronounce.py` | 読み方の辞書。単位・上付き・ギリシャ文字は組み込みで直し（7 μm → 7 micrometers）、略語などは自分で登録。音声に渡す文だけに使う |
| `glossary.py` | 論文ごとの専門用語の一覧（辞書に無い語・略語と元の語・よく出る句）と端末内の訳。辞書に無い語はこれで引け、単語帳に登録できる |
| `audio.py` | `say` → WAV → `afconvert` で m4a（AAC）。1文ずつと、持ち出し用の章ごと・全体の1本もの。文ごとの音声は声・速さ・文を鍵に `audio/cache/` に控え、章を編集して番号がずれても同じ文は作り直さない |
| `translate.py` `tools/mac_translate.swift` | 文ごとの日本語訳（原文ごとの控え `translation_cache.json` で、同じ文は訳し直さない）。macOS 内蔵の翻訳（Translation フレームワーク、端末内で動き文は外に出ない）。初回に swiftc で `.bin/` に作る。英語・日本語の翻訳データはシステム設定 → 一般 → 言語と地域 →「翻訳言語…」で入れる |
| `lookup.py` | 単語を引く。EJDict → 活用を戻す → 派生語を戻す（近い語）→ Free Dictionary API（英英、ネット） |
| `vocab.py` | 単語帳（`vocab.sqlite`）。辞書で引いて「登録」した語を入れる。答えの記録（reviews）が復習の正本 |
| `srs.py` | 復習の計算（py-fsrs、FSRS-6 の既定値、fuzz なし）。新しい語は1日20語まで |
| `bundle.py` | スマホへ渡す zip と、スマホから戻る記録（JSON）の形 |
| `share.py` | 同じ Wi‑Fi のスマホへの一時的な受け渡しページ（合言葉付きのアドレス、10分で閉じる） |
| `ai_fix_prompt.md` `tools/check_paper.py` | AI に手直しを頼むときの決まった依頼文と、手直し後の確認コマンド。画面の「AI に手直しを頼む」で「フォルダの場所 ＋ 依頼文」をコピーできる。手直し済み（`sentences.json` の `manual`）は自動の取り出し直しで上書きしない。文が変わると音声・訳を作り直す（中身の目印 `items_hash`） |
| `server.py` | 画面と API を 127.0.0.1 で配る（標準ライブラリの `http.server`） |
| `index.html` `assets/` | 画面（読む・復習・スマホ）。`assets/shared.js` は図への参照・しおりの戻り先・単語の分け方をスマホ版と共有する（`pwa/js/shared.js` は同じ内容の写し）。「テキストを貼り付け」、「章を編集」（見出しの &lt; &gt;・名前・前とつなぐ、文の「ここで分ける」）、論文ごとの「図・表・数式」の一覧と、大きく開く表示 |
| `tools/` | 取り込み（`import_pdf.py`）、辞書づくり（`build_dict.py` `build_pwa_dict.py`）、架空のサンプル PDF（`make_sample_pdf.py`）、取り出し・図の切り抜きの点数（`eval_extract.py` `eval_figures.py`: 手直し済みの論文を正解として比べる） |

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
| `sw.js` `manifest.webmanifest` `icons/` | オフライン動作とホーム画面への追加。画面とプログラムはつながれば必ず新しいものを取る（古いプログラムと新しい画面が混ざらないように） |

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
