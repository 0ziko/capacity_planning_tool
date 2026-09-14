"""Yalnizca test: tek kaynak uzerinde 4 is icin tum siralarin termin gecikme toplami."""

from __future__ import annotations

import itertools
from datetime import date, timedelta


def sequential_lateness_days(
    job_hours: list[float],
    due_dates: list[date],
    perm: tuple[int, ...],
    *,
    week_start: date,
    hours_per_day: float = 8.0,
) -> float:
    """Basit ardisik zamanlama: gecikme gun toplami (referans optimum arama)."""
    cum_h = 0.0
    total_late = 0.0
    for idx in perm:
        cum_h += job_hours[idx]
        day_offset = int((cum_h - 1e-9) / hours_per_day)
        end = week_start + timedelta(days=day_offset)
        due = due_dates[idx]
        if end > due:
            total_late += (end - due).days
    return total_late


def best_and_worst_lateness(job_hours: list[float], due_dates: list[date], *, week_start: date) -> tuple[float, float, tuple[int, ...]]:
    n = len(job_hours)
    perms = list(itertools.permutations(range(n)))
    scores = [(sequential_lateness_days(job_hours, due_dates, p, week_start=week_start), p) for p in perms]
    scores.sort(key=lambda x: x[0])
    return scores[0][0], scores[-1][0], scores[0][1]
