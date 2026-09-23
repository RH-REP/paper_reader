# paper_reader（開発）

英語論文の PDF を**章ごと**に分け、Mac の読み上げ音声で章単位・全体を読み上げるローカル app。
いまはデモ段階。辞書引きと単語帳（FSRS で復習）は次の段階で足す。

- 利用側：`app/learning/paper_reader/`（起動口・設定・取り込んだ論文）。ここにはコードと架空のサンプルだけを置く

## 構成

| ファイル | 役目 |
|---|---|
| `extract.py` | PDF → 題名・章・文。文字層が無いページは Tesseract で OCR。見出しはフォント（太字・本文より大きい）で見分け、OCR ページは「1 INTRODUCTION」型の番号で見分ける |
| `store.py` | `data/papers/<sha8>/` への取り込み（元の PDF はコピーするだけ）・一覧・読み出し |
| `server.py` | 画面と API を 127.0.0.1 で配る（標準ライブラリの `http.server`） |
| `index.html` `assets/` | 画面。読み上げはブラウザの Web Speech API（中身は Mac の読み上げ音声） |
| `tools/import_pdf.py` | 画面を使わずに取り込む |
| `tools/make_sample_pdf.py` | 架空の2段組論文 `samples/sample_paper.pdf` を作る |
| `tests/` | サンプルでの章・文・引用除去・取り込み・OCR のテスト |

## 開発

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
cp config.example.json config.json && .venv/bin/python server.py --config config.json   # samples で試すときは samples/ の PDF を画面から取り込む
```

- 実在の論文はここに入れない。実データで確かめるときは `app/learning/paper_reader/` に複製して、そこで取り込む。
- OCR には Tesseract（`brew install tesseract`、英語データ `eng`）が要る。無ければ文字層の無いページは飛ばす。
- 直したら `app/learning/paper_reader/update.command` で複製する。`data/` `config.json` は複製されない（`.deployignore`）。

## 章の分け方の前提

- 本文の文字サイズ（文字数で重みをつけた最頻値）より小さい行は読まない（図表の説明・欄外・注）。
- 3ページ以上の上下余白に同じ文字列が出たら、ヘッダー・フッターとして捨てる。
- 2段組は、行の左端がページ幅の 45% より右なら右の段とみなす。
- `REFERENCES` `ACKNOWLEDGMENTS` `FUNDING` などから後ろは「後付け」。全体を読むときに飛ばせる。
- 読み上げでは `(Smith et al., 2020)` のような年入りの括弧と `[12]` 型の引用を落とせる（画面の表示はそのまま）。
- 取り出し方を変えたら `extract.EXTRACTOR_VERSION` を上げる。開いたときに作り直される。

## ライセンスの注意

PyMuPDF は AGPL。手元で使う分には問題ない。GitHub に公開するならソース公開が前提になる（registry では `github_allowed: false`）。
