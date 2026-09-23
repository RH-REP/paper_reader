// 復習の計算（FSRS）。Mac 版（py-fsrs 6.3、fuzz なし）の Scheduler.review_card を JavaScript に写したもの。
// 同じ記録からは Mac と同じ状態・同じ期限になる（tests/test_review_share.py と同じ例で確かめてある）。
// py-fsrs: MIT License, Copyright (c) 2022 Open Spaced Repetition  https://github.com/open-spaced-repetition/py-fsrs
//
// 状態は保存しない。答えの記録（Mac の分＋スマホの分）を時刻順に当てて、その場で作る。
export const W = [0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796, 1.4835,
  0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542];
export const NEW_PER_DAY = 20;
const RETENTION = 0.9;
const MAX_INTERVAL = 36500;
const MIN = 60 * 1000, DAY = 86400 * 1000;
const LEARNING_STEPS = [1 * MIN, 10 * MIN];
const RELEARNING_STEPS = [10 * MIN];
const S_MIN = 0.001;
const DECAY = -W[20];
const FACTOR = 0.9 ** (1 / DECAY) - 1;
export const State = { Learning: 1, Review: 2, Relearning: 3 };
const AGAIN = 1, HARD = 2, GOOD = 3, EASY = 4;

const clampD = (d) => Math.min(Math.max(d, 1), 10);
const clampS = (s) => Math.max(s, S_MIN);
// Python の round（偶数への丸め）に合わせる
function pyRound(x) {
  const f = Math.floor(x), diff = x - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}
const initS = (r) => clampS(W[r - 1]);
const initD = (r, clamp) => { const d = W[4] - Math.E ** (W[5] * (r - 1)) + 1; return clamp ? clampD(d) : d; };
function nextInterval(s) {
  const days = pyRound((s / FACTOR) * (RETENTION ** (1 / DECAY) - 1));
  return Math.min(Math.max(days, 1), MAX_INTERVAL) * DAY;
}
function shortTermS(s, r) {
  let inc = Math.E ** (W[17] * (r - 3 + W[18])) * s ** -W[19];
  if (r >= HARD) inc = Math.max(inc, 1);
  return clampS(s * inc);
}
function nextD(d, r) {
  const delta = -(W[6] * (r - 3));
  const arg2 = d + ((10 - d) * delta) / 9;
  return clampD(W[7] * initD(EASY, false) + (1 - W[7]) * arg2);
}
function retrievability(card, at) {
  if (!card.last_review || card.stability == null) return 0;
  const elapsed = Math.max(0, Math.floor((at - card.last_review) / DAY));
  return (1 + (FACTOR * elapsed) / card.stability) ** DECAY;
}
function nextS(d, s, R, r) {
  let ns;
  if (r === AGAIN) {
    const longTerm = W[11] * d ** -W[12] * ((s + 1) ** W[13] - 1) * Math.E ** ((1 - R) * W[14]);
    ns = Math.min(longTerm, s / Math.E ** (W[17] * W[18]));
  } else {
    const hard = r === HARD ? W[15] : 1, easy = r === EASY ? W[16] : 1;
    ns = s * (1 + Math.E ** W[8] * (11 - d) * s ** -W[9] * (Math.E ** ((1 - R) * W[10]) - 1) * hard * easy);
  }
  return clampS(ns);
}

export function newCard() {
  return { state: State.Learning, step: 0, stability: null, difficulty: null, due: null, last_review: null };
}

// 1回答えたあとの状態（card は変えずに新しいものを返す）
export function answer(card, r, at) {
  const c = { ...(card || newCard()) };
  const t = at.getTime();
  const sinceDays = c.last_review != null ? Math.floor((t - c.last_review) / DAY) : null;
  const R = retrievability(c, t);
  let interval;
  const steps = c.state === State.Relearning ? RELEARNING_STEPS : LEARNING_STEPS;
  if (c.state === State.Learning || c.state === State.Relearning) {
    if (c.stability == null || c.difficulty == null) {
      c.stability = initS(r);
      c.difficulty = initD(r, true);
    } else if (sinceDays != null && sinceDays < 1) {
      c.stability = shortTermS(c.stability, r);
      c.difficulty = nextD(c.difficulty, r);
    } else {
      c.stability = nextS(c.difficulty, c.stability, R, r);
      c.difficulty = nextD(c.difficulty, r);
    }
    if (steps.length === 0 || (c.step >= steps.length && r !== AGAIN)) {
      c.state = State.Review; c.step = null; interval = nextInterval(c.stability);
    } else if (r === AGAIN) {
      c.step = 0; interval = steps[0];
    } else if (r === HARD) {
      interval = c.step === 0 && steps.length === 1 ? steps[0] * 1.5
        : c.step === 0 && steps.length >= 2 ? (steps[0] + steps[1]) / 2 : steps[c.step];
    } else if (r === GOOD) {
      if (c.step + 1 === steps.length) { c.state = State.Review; c.step = null; interval = nextInterval(c.stability); }
      else { c.step += 1; interval = steps[c.step]; }
    } else {
      c.state = State.Review; c.step = null; interval = nextInterval(c.stability);
    }
  } else {
    c.stability = sinceDays != null && sinceDays < 1 ? shortTermS(c.stability, r) : nextS(c.difficulty, c.stability, R, r);
    c.difficulty = nextD(c.difficulty, r);
    if (r === AGAIN && RELEARNING_STEPS.length) {
      c.state = State.Relearning; c.step = 0; interval = RELEARNING_STEPS[0];
    } else {
      interval = nextInterval(c.stability);
    }
  }
  c.due = t + interval;
  c.last_review = t;
  return c;
}

export function replay(logs) {
  let card = null;
  for (const lg of [...logs].sort((a, b) => (new Date(a.reviewed_at) - new Date(b.reviewed_at)))) {
    card = answer(card, Number(lg.rating), new Date(lg.reviewed_at));
  }
  return card;
}

export function human(ms) {
  const s = ms / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}分`;
  if (s < 86400) return `${Math.round(s / 3600)}時間`;
  const d = s / 86400;
  if (d < 30) return `${Math.round(d)}日`;
  if (d < 365) return `${(d / 30).toFixed(1).replace(/\.0$/, "")}か月`;
  return `${(d / 365).toFixed(1).replace(/\.0$/, "")}年`;
}

export function preview(card, at) {
  const out = {};
  for (const r of [1, 2, 3, 4]) out[r] = human(answer(card, r, at).due - at.getTime());
  return out;
}

export function stateName(card) {
  if (!card) return "New";
  return { 1: "Learning", 2: "Review", 3: "Relearning" }[card.state];
}

// 単語帳と記録から、今出す語と残りの数を作る（Mac 版 Vocab.queue と同じ順）
export function queue(words, reviews, now = new Date(), newPerDay = NEW_PER_DAY) {
  const byWord = new Map();
  for (const r of reviews) {
    if (!byWord.has(r.headword)) byWord.set(r.headword, []);
    byWord.get(r.headword).push(r);
  }
  const start = new Date(now); start.setHours(0, 0, 0, 0);
  let newToday = 0;
  for (const logs of byWord.values()) {
    const first = Math.min(...logs.map((l) => new Date(l.reviewed_at).getTime()));
    if (first >= start.getTime()) newToday++;
  }
  const newLeft = Math.max(0, newPerDay - newToday);
  const due = [], fresh = [], later = [];
  for (const w of words) {
    const logs = byWord.get(w.headword);
    const card = logs ? replay(logs) : null;
    if (!card) fresh.push(w);
    else if (card.due <= now.getTime()) due.push({ w, card });
    else later.push(card.due);
  }
  due.sort((a, b) => a.card.due - b.card.due);
  fresh.sort((a, b) => new Date(a.first_seen) - new Date(b.first_seen));   // Mac は現地時刻、スマホは UTC の書式なので時刻で比べる
  const learning = due.filter((d) => d.card.state !== State.Review).length;
  const counts = { new: Math.min(fresh.length, newLeft), learning, review: due.length - learning, total: words.length,
                   new_waiting: fresh.length };
  let pick = null;
  if (due.length) pick = due[0];
  else if (fresh.length && newLeft) pick = { w: fresh[0], card: null };
  return {
    counts,
    next_due: later.length ? new Date(Math.min(...later)) : null,
    card: pick && { ...pick.w, card: pick.card, state: stateName(pick.card),
                    reviews: (byWord.get(pick.w.headword) || []).length, intervals: preview(pick.card, now) },
  };
}
