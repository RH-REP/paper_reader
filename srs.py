"""復習の計算（FSRS。py-fsrs）。

復習の「状態」は保存の正本にしない。正本は答えの記録（いつ・どの語に・どう答えたか）で、状態はそれを
時刻順に計算し直して作る。Mac とスマホ（PWA、ts-fsrs）の記録を合わせても食い違わないようにするため。
- 間隔のゆらぎ（fuzz）は切る（同じ記録から同じ状態になるように）
- パラメータは FSRS-6 の既定。スマホ側（pwa/app.js の FSRS_PARAMS）も同じ値を使う
- 新しい語（まだ一度も答えていない語）は1日 NEW_PER_DAY 語まで出す（Anki の既定と同じ 20）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler

W = (0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796, 1.4835,
     0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542)
DESIRED_RETENTION = 0.9
LEARNING_STEPS = (timedelta(minutes=1), timedelta(minutes=10))
RELEARNING_STEPS = (timedelta(minutes=10),)
MAX_INTERVAL_DAYS = 36500
NEW_PER_DAY = 20
RATINGS = {1: Rating.Again, 2: Rating.Hard, 3: Rating.Good, 4: Rating.Easy}
LABELS = {1: "もう一度", 2: "難しい", 3: "正解", 4: "簡単"}

SCHEDULER = Scheduler(parameters=W, desired_retention=DESIRED_RETENTION, learning_steps=LEARNING_STEPS,
                      relearning_steps=RELEARNING_STEPS, maximum_interval=MAX_INTERVAL_DAYS, enable_fuzzing=False)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse(ts: str) -> datetime:
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def replay(logs: list[dict]) -> Card | None:
    """1語ぶんの記録（rating, reviewed_at）を時刻順に当てて、今の状態を返す。記録が無ければ None（新しい語）。"""
    card = None
    for lg in sorted(logs, key=lambda x: x["reviewed_at"]):
        card = card or Card(card_id=1)
        card, _ = SCHEDULER.review_card(card, RATINGS[int(lg["rating"])], parse(lg["reviewed_at"]))
    return card


def answer(card: Card | None, rating: int, at: datetime) -> Card:
    card, _ = SCHEDULER.review_card(card or Card(card_id=1), RATINGS[rating], at)
    return card


def human(delta: timedelta) -> str:
    s = delta.total_seconds()
    if s < 3600:
        return f"{max(1, round(s / 60))}分"
    if s < 86400:
        return f"{round(s / 3600)}時間"
    d = s / 86400
    if d < 30:
        return f"{round(d)}日"
    if d < 365:
        return f"{d / 30:.1f}か月".replace(".0か", "か")
    return f"{d / 365:.1f}年".replace(".0年", "年")


def preview(card: Card | None, at: datetime) -> dict[int, str]:
    """4つのボタンそれぞれを押したときの次回までの間隔（ボタンに出す）。"""
    return {r: human(answer(card, r, at).due - at) for r in RATINGS}
