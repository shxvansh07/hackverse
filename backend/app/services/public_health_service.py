"""Anonymized, aggregate symptom-trend signal across all handed-off cases.

Nothing here writes anything new, and nothing here ever returns a case_id,
patient_id, or free-text transcript — only a symptom name and day-bucketed
counts. That's enforced by construction: compute_trends() only ever reads
case.symptoms/associated_symptoms and case.created_at, and drops any symptom
whose total count in the window is too thin to aggregate anonymously
(MIN_BUCKET_COUNT) before it is ever turned into a SymptomTrend.

The point of this module: individual visits are triaged one at a time
elsewhere in this codebase, but nothing previously looked at them in
aggregate. A symptom that is unusually common today, compared to its own
recent baseline, is exactly the kind of local-outbreak signal a single
patient's triage can never surface on its own.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from app.shared.database import db
from app.shared.models import PublicHealthTrendsResponse, SymptomTrend, SymptomTrendPoint

#: A symptom with fewer than this many total cases in the window is dropped
#: entirely rather than shown — too thin a count to aggregate anonymously,
#: and too thin to mean anything statistically either way.
MIN_BUCKET_COUNT = 3

#: Distinct baseline days (i.e. days before today, within the window) needed
#: before a ratio is trusted. Below this, insufficient_history=True and the
#: symptom is shown as raw counts only — a same-day demo has zero baseline
#: days, and a fabricated ratio from zero history would be a guess, not a
#: signal.
MIN_BASELINE_DAYS = 3

#: recent_count / baseline_avg at or above this is a flagged spike.
FLAG_RATIO = 2.0


def _day(iso_timestamp: str) -> str:
    return iso_timestamp.split("T")[0]


class PublicHealthService:
    @staticmethod
    def compute_trends(days: int = 14) -> PublicHealthTrendsResponse:
        """Day-bucketed case counts per symptom over the last `days` days.

        Only case.handed_off cases count — the same boundary
        database.list_cases() already uses for the doctor queue, so a
        session still mid-conversation (never reviewed, possibly abandoned)
        can't contribute to a public signal.
        """
        today = datetime.now(timezone.utc).date()
        window_start = today - timedelta(days=days - 1)

        # symptom -> day -> count
        counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for case in db.cases.values():
            if not case.handed_off:
                continue
            day = _day(case.created_at)
            if day < window_start.isoformat() or day > today.isoformat():
                continue
            symptoms = {
                s.strip().lower() for s in (*case.symptoms, *case.associated_symptoms) if s.strip()
            }
            for symptom in symptoms:
                counts[symptom][day] += 1

        today_str = today.isoformat()
        trends: List[SymptomTrend] = []

        for symptom, by_day in counts.items():
            total = sum(by_day.values())
            if total < MIN_BUCKET_COUNT:
                continue

            daily_counts = [
                SymptomTrendPoint(date=day, count=count)
                for day, count in sorted(by_day.items())
            ]
            recent_count = by_day.get(today_str, 0)
            baseline_days = {day: count for day, count in by_day.items() if day != today_str}

            insufficient_history = len(baseline_days) < MIN_BASELINE_DAYS
            baseline_avg = (
                sum(baseline_days.values()) / len(baseline_days) if baseline_days else 0.0
            )
            ratio = (
                None if insufficient_history or baseline_avg == 0
                else recent_count / baseline_avg
            )
            flagged = bool(not insufficient_history and ratio is not None and ratio >= FLAG_RATIO)

            trends.append(
                SymptomTrend(
                    symptom=symptom,
                    total_count=total,
                    daily_counts=daily_counts,
                    baseline_avg=round(baseline_avg, 2),
                    recent_count=recent_count,
                    ratio=round(ratio, 2) if ratio is not None else None,
                    flagged=flagged,
                    insufficient_history=insufficient_history,
                )
            )

        # Flagged spikes first, then by total volume — the doctor's
        # attention should land on real anomalies before routine
        # high-volume symptoms.
        trends.sort(key=lambda t: (not t.flagged, -t.total_count))

        return PublicHealthTrendsResponse(
            generated_at=datetime.now(timezone.utc).isoformat(),
            window_days=days,
            min_bucket_count=MIN_BUCKET_COUNT,
            trends=trends,
        )
