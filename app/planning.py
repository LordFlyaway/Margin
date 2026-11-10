"""Pure scheduling logic: spaced repetition and the exam revision plan."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

AGAIN, HARD, GOOD, EASY = 0, 1, 2, 3
_MIN_EASE = 1.3


@dataclass
class CardState:
    ease: float
    interval_days: float
    reps: int
    lapses: int


def review(card: CardState, grade: int) -> CardState:
    """SM-2 style update. Returns the new state; interval_days is days until the next review."""
    if grade == AGAIN:
        return CardState(max(_MIN_EASE, card.ease - 0.2), 0, 0, card.lapses + 1)
    ease = card.ease + {HARD: -0.15, GOOD: 0.0, EASY: 0.15}[grade]
    ease = max(_MIN_EASE, ease)
    if card.reps == 0:
        interval = {HARD: 1, GOOD: 1, EASY: 3}[grade]
    elif card.reps == 1:
        interval = {HARD: 3, GOOD: 4, EASY: 7}[grade]
    else:
        factor = {HARD: 1.2, GOOD: ease, EASY: ease * 1.3}[grade]
        interval = max(card.interval_days + 1, round(card.interval_days * factor))
    return CardState(ease, interval, card.reps + 1, card.lapses)


_LEARN, _LEARN_WEAK, _REVIEW, _MOCK = 45, 60, 20, 60
_REVIEW_GAPS = (1, 3, 7)


def build_plan(topics: list[str], exam: date, today: date, minutes_per_day: int,
               weak: set[str] | None = None, already_learned: set[str] | None = None) -> list[dict]:
    """Lay out learn sessions early, spaced reviews after each, and a mock exam the day before.

    Weak topics go first and get longer sessions. Returns [{date, topic, kind, minutes}].
    """
    weak = weak or set()
    learned = already_learned or set()
    days = [today + timedelta(days=i) for i in range((exam - today).days)]
    if not days:
        raise ValueError("The exam date has to be after today.")

    capacity = {d: minutes_per_day for d in days}
    tasks: list[dict] = []

    def add(d: date, topic: str, kind: str, minutes: int) -> None:
        tasks.append({"date": d.isoformat(), "topic": topic, "kind": kind, "minutes": minutes})
        capacity[d] -= minutes

    final = days[-1]
    if len(days) > 1 or minutes_per_day >= _MOCK:
        add(final, "Full mock exam", "mock", min(_MOCK, minutes_per_day))

    to_learn = sorted((t for t in topics if t not in learned), key=lambda t: t not in weak)
    learn_days = days[:-1] if len(days) > 1 else days
    # Keep the last ~30% of a long runway free for review.
    learn_days = learn_days[:max(1, round(len(learn_days) * 0.7))]

    need = sum(_LEARN_WEAK if t in weak else _LEARN for t in to_learn)
    room = sum(max(0, capacity[d]) for d in learn_days)
    squeeze = min(1.0, room / need) if need else 1.0

    placed: list[tuple[str, date]] = []
    cursor = 0
    for topic in to_learn:
        minutes = max(15, round((_LEARN_WEAK if topic in weak else _LEARN) * squeeze))
        while cursor < len(learn_days) - 1 and capacity[learn_days[cursor]] < minutes:
            cursor += 1
        d = learn_days[cursor]
        add(d, topic, "learn", minutes)
        placed.append((topic, d))

    for topic, learned_on in placed:
        for gap in _REVIEW_GAPS:
            target = learned_on + timedelta(days=gap)
            slot = next((d for d in days if d >= target and capacity[d] >= _REVIEW), None)
            if slot is not None and slot != final:
                add(slot, topic, "review", _REVIEW)

    return sorted(tasks, key=lambda t: t["date"])
