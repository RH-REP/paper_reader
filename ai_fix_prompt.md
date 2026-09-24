上のフォルダは、英語論文の読み上げ app「paper_reader」が取り込んだ論文1本です。
PDF から自動で取り出した「章と文」（sentences.json）に誤りがある場合があるので、元の PDF と突き合わせて手直ししてください。

## フォルダの中身
- original.pdf … 元の論文。見るだけで変更しない
- sentences.json … 自動で取り出した章と文。**これを直す**
- meta.json、audio/、export/、translation.json … 触らない（app が作り直す。訳もこちらで作り直すので直さない）

## 手順
1. 最初に sentences.json を sentences.orig.json にコピーして残す（sentences.orig.json が既にあれば上書きしない）
2. original.pdf を全ページ読み、sentences.json と突き合わせる。文字が選べない（画像だけの）ページは OCR で読む（`tesseract` が使えれば使い、無ければ画像として読む）
3. 下の「直す観点」のとおりに直して、sentences.json に書き戻す
4. 確認のコマンドを実行し、エラーが無くなるまで直す（警告は、PDF どおりなら残してよい）:
   {check}
5. 最後に、直した内容を箇条書きで報告する（章の数・文の数の前後、直した種類ごとの件数と例）

## 直す観点
1. **章分け**: PDF の見出しどおりに sections を分ける。見出しの文言と番号は PDF のまま（例 "2.1 Pupil Size"、"1. Introduction"）。level は 1 = 章、2 = 節、3 = その下。最初の見出しより前の要旨は title "Abstract"、kind "front"
2. **後付け**: References / Acknowledgments / Funding / Author contributions / Data availability / Conflict of interest / Appendix などから後ろは kind "back"。本文は kind "body"
3. **単語の区切り**: 空白の抜け（limitedbysaturation → limited by saturation）、行末ハイフンの残り・誤結合（micro- electromechanical、deform- able）、合字の化け（ﬁ → fi）を直す。つづりや言い回しそのものは変えない
4. **文の切れ目**: sentences の1要素は1文。Eq. / Fig. / Ref. / et al. / e.g. / i.e. / 「(b) and (c)」で切らない。2文が1つにつながっていたら分ける
5. **混ざったものを外す**: 図表の説明（"Fig. 3. …"）、表の中身、ページのヘッダー・フッター・ページ番号、著者名・所属、受付日、著作権表示、脚注番号の切れ端は文から外す
6. **抜けを補う**: PDF にあるのに抜けている本文（取り出し漏れ・画像だけのページ）は補う。段組みの読む順番が入れ替わっていたら直す
7. **数式**: 画面に出す t は PDF の見た目に近い形で残してよい。読み上げる s では、式を "(equation)" に置き換え、文字化け（ð, Þ, 1⁄4, � など）を s に残さない
8. **読み上げる文 s**: t から、年を含む括弧の引用（Smith et al., 2020）と [12] 型の引用を除いたもの（式は 7 のとおり）。s を空文字 "" にすると、その文は読み上げない（表の切れ端などに使う）

## sentences.json の形（守ること）
```json
{
  "title": "論文の題名",
  "manual": {"by": "ai", "at": "<作業した今の日時（ISO 8601、例 YYYY-MM-DDThh:mm:ss+09:00）>", "notes": "<直した内容の要約>"},
  "sections": [
    {"title": "1. Introduction", "level": 1, "kind": "body",
     "sentences": [{"t": "画面に出す文。", "s": "読み上げる文。"}]}
  ]
}
```
- 最上位の **"manual" を必ず付ける**（これが無いと、app の自動の取り出し直しで上書きされる）
- "at" には作業した今の日時を入れる（`date -Iseconds` などで調べる。例の形をそのまま写さない）。"notes" には実際に直した内容を書く
- 上の title / manual / sections / level / kind / sentences / t / s 以外のキー（id, number, speech_title, page, extractor_version など）は残しても消してもよい（app が付け直す）
- UTF-8 の正しい JSON にする

app は次にこの論文を開いたとき、文が変わったことを見つけて、音声と訳を作り直します。
