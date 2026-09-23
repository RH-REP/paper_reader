# paper_reader（開発）

英語論文の PDF を**章ごと**に分け、Mac の `say` で作った音声ファイル（m4a）で章単位・全体を読み上げるローカル app。
文中の単語をクリックすると英和辞書（EJDict）で引き、引いた単語は単語帳に自動で入る。
章ごとの m4a を書き出すので、iPhone・Android にも持ち出して聞ける。単語帳の復習（FSRS）は次の段階で足す。

- 利用側：`app/learning/paper_reader/`（起動口・設定・取り込んだ論文）。ここにはコードと架空のサンプルだけを置く

## 構成

| ファイル | 役目 |
|---|---|
| `extract.py` | PDF → 題名・章・文。文字層が無いページは Tesseract で OCR。見出しはフォント（太字・本文より大きい）で見分け、OCR ページは「1 INTRODUCTION」型の番号で見分ける |
| `store.py` | `data/papers/<sha8>/` への取り込み（元の PDF はコピーするだけ）・一覧・読み出し |
| `audio.py` | `say` → WAV → `afconvert` で m4a（AAC）。1文ずつと、持ち出し用の章ごと・全体の1本もの |
| `lookup.py` | 単語を引く。EJDict → 活用を戻す → 派生語を戻す（近い語）→ Free Dictionary API（英英、ネット） |
| `vocab.py` | 単語帳（`vocab.sqlite`）。引いて見つかった語を自動で保存。復習用の列（card, due）は空けてある |
| `server.py` | 画面と API を 127.0.0.1 で配る（標準ライブラリの `http.server`）。取り込むと裏で音声をまとめて作る。音声は Range 対応で配る（Safari 用） |
| `index.html` `assets/` | 画面。音声ファイルを順に鳴らす。音声ができるまではブラウザの読み上げで代わりに読む |
| `tools/import_pdf.py` | 画面を使わずに取り込む（音声も作る） |
| `tools/build_dict.py` | EJDict-hand（CC0）を GitHub から取って `<data_root>/dict/ejdict.sqlite` を作る |
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
- 音声は macOS の `say` と `afconvert` を使う（どちらも標準）。論文1本（10ページ・162本）で約1分40秒（4並列）。
- 辞書は `tools/build_dict.py` で作る。テストは架空の小さな辞書を作って使う。
- 直したら `app/learning/paper_reader/update.command` で複製する。`data/` `config.json` は複製されない（`.deployignore`）。

## 章の分け方の前提

- 本文の文字サイズ（文字数で重みをつけた最頻値）より小さい行は読まない（図表の説明・欄外・注）。
- 3ページ以上の上下余白に同じ文字列が出たら、ヘッダー・フッターとして捨てる。
- 2段組は、行の左端がページ幅の 45% より右なら右の段とみなす。
- `REFERENCES` `ACKNOWLEDGMENTS` `FUNDING` などから後ろは「後付け」。全体を読むときに飛ばせる。
- 読み上げ（音声ファイル）では `(Smith et al., 2020)` のような年入りの括弧と `[12]` 型の引用を落とす（画面の表示はそのまま）。
- 取り出し方を変えたら `extract.EXTRACTOR_VERSION` を上げる。開いたときに作り直される。

## ライセンスの注意

PyMuPDF は AGPL。手元で使う分には問題ない。GitHub に公開するならソース公開が前提になる（registry では `github_allowed: false`）。
