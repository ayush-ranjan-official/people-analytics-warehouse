"""ATS data: applications and their stage history for every requisition.

Applicants are drawn per requisition from a generator seeded by the requisition id, so a
requisition's applicants never change when the simulation runs further. The hired applicant's
path is worked backwards from the date the offer was accepted; everyone else moves forward from
the date they applied until they are rejected, withdraw, decline an offer, or the requisition closes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np

from generator.workforce import Requisition, Workforce, normalised

STAGES = ("applied", "screen", "interview", "offer", "hired")


@dataclass
class Application:
    application_id: str
    req: Requisition
    candidate_id: str
    source: str
    events: list[tuple[str, datetime]]


def _ts(rng: np.random.Generator, d: date) -> datetime:
    return datetime.combine(d, time(int(rng.integers(8, 19)), int(rng.integers(0, 60)), int(rng.integers(0, 60))))


def _in_order(events: list[tuple[str, datetime]]) -> list[tuple[str, datetime]]:
    """Dates never go backwards along a path; sorting the times fixes same-day stages drawn out of order."""
    times = sorted(ts for _, ts in events)
    return [(stage, ts) for (stage, _), ts in zip(events, times, strict=True)]


class Recruiting:
    def __init__(self, wf: Workforce):
        self.wf = wf
        rc = wf.cfg["recruiting"]
        self.rc = rc
        self.sources = list(rc["sources"])
        self.source_shares = normalised([rc["sources"][s]["share"] for s in self.sources])
        quality = np.array([rc["sources"][s]["quality"] for s in self.sources], dtype=float)
        self.quality = dict(zip(self.sources, quality / float(self.source_shares @ quality), strict=True))
        # Source quality applies at screening, so better sources produce proportionally more hires.
        self.hire_source_shares = normalised(self.source_shares * np.array([self.quality[s] for s in self.sources]))
        conv = rc["stage_conversion"]
        self.p_screen, self.p_interview = conv["screen"], conv["interview"]
        # Declined offers per hire, spread over everyone else who reaches interview. Applicants stop
        # moving when the requisition closes, so only part of these offers happen; scale back up.
        declines_per_hire = 1 / rc["offer_acceptance"] - 1
        self.p_offer_declined = declines_per_hire / (rc["applicants_per_hire"] * self.p_screen * self.p_interview
                                                     * rc["offer_before_close_share"])

    def days(self, rng: np.random.Generator, stage: str) -> int:
        lo, hi = self.rc["stage_days"][stage]
        return int(rng.integers(lo, hi + 1))

    def build(self) -> list[Application]:
        """Every application ever drawn, with its full stage timeline (not yet cut at a date)."""
        apps: list[Application] = []
        candidate_pool: list[str] = []
        next_app, next_cand = 1_000_000, 100_000
        for req in sorted(self.wf.requisitions.values(), key=lambda r: r.req_id):
            rng = self.wf.rng("applications", req.req_id)
            window = max(1, (req.closed_date - req.opened_date).days)
            if req.outcome == "Filled":
                plans = [("hire", str(rng.choice(self.sources, p=self.hire_source_shares)))]
                n_others = int(rng.poisson(self.rc["applicants_per_hire"] - 1))
            else:
                plans = []
                n_others = int(rng.poisson(self.rc["applicants_per_hire"] * min(1.0, window / 45)))
            plans += [("other", str(rng.choice(self.sources, p=self.source_shares))) for _ in range(n_others)]

            new_candidates: list[str] = []
            on_this_req: set[str] = set()
            for kind, source in plans:
                next_app += 1
                candidate = None
                if (kind == "other" and len(candidate_pool) > len(on_this_req)
                        and rng.random() < self.rc["repeat_candidate_share"]):
                    i = int(rng.integers(len(candidate_pool)))
                    while candidate_pool[i] in on_this_req:      # one application per person per requisition
                        i = (i + 1) % len(candidate_pool)
                    candidate = candidate_pool[i]
                if candidate is None:
                    next_cand += 1
                    candidate = f"C{next_cand}"
                    if kind == "other":
                        new_candidates.append(candidate)
                on_this_req.add(candidate)
                path = self._hired_path(req, rng) if kind == "hire" else self._other_path(req, source, rng, window)
                apps.append(Application(f"A{next_app}", req, candidate, source, path))
            candidate_pool.extend(new_candidates)       # rejected or withdrawn candidates may apply again
        return apps

    def _hired_path(self, req: Requisition, rng: np.random.Generator) -> list[tuple[str, datetime]]:
        """Worked backwards from the accepted offer; gaps shrink to fit a requisition filled quickly."""
        gaps = [self.days(rng, stage) for stage in ("screen", "interview", "offer", "decision")]
        room = (req.closed_date - req.opened_date).days
        if sum(gaps) > room:
            gaps = [max(1, g * room // sum(gaps)) for g in gaps]
        dates = [req.closed_date]
        for gap in reversed(gaps):
            dates.insert(0, max(req.opened_date, dates[0] - timedelta(days=gap)))
        return _in_order([(stage, _ts(rng, d)) for stage, d in zip(STAGES, dates, strict=True)])

    def _other_path(self, req: Requisition, source: str, rng: np.random.Generator,
                    window: int) -> list[tuple[str, datetime]]:
        q = self.quality[source]
        applied = req.opened_date + timedelta(days=int(max(0, window - 5) * rng.beta(1, 2)))   # most apply early
        events = [("applied", _ts(rng, applied))]
        d, outcome = applied, "rejected"
        for stage, p in (("screen", self.p_screen * q), ("interview", self.p_interview),
                         ("offer", self.p_offer_declined)):
            if rng.random() >= min(0.95, p):
                break
            nxt = d + timedelta(days=self.days(rng, stage))
            if nxt >= req.closed_date:            # the requisition was filled or cancelled first
                break
            d = nxt
            events.append((stage, _ts(rng, d)))
            if stage == "offer":
                outcome = "offer_declined"
        if outcome == "rejected" and rng.random() < self.rc["withdraw_share"]:
            outcome = "withdrawn"
        end = d + timedelta(days=self.days(rng, "decision" if outcome == "offer_declined" else "screen"))
        if req.outcome == "Cancelled":
            end = min(end, req.closed_date)
        else:
            end = min(end, req.closed_date + timedelta(days=self.rc["cleanup_days_after_fill"]))
        events.append((outcome, _ts(rng, max(end, d))))
        return _in_order(events)


def materialize_ats(apps: list[Application], as_of: date) -> dict[str, list[dict]]:
    """The ATS as it looks at the end of `as_of`: only events that have happened by then."""
    applications, stages = [], []
    for app in apps:
        seen = [(s, ts) for s, ts in app.events if ts.date() <= as_of]
        if not seen:
            continue
        req = app.req
        hired_emp = None
        if seen[-1][0] == "hired" and req.hire_date and req.hire_date <= as_of:
            hired_emp = req.hired_employee_id     # the HRIS id exists only once they start
        applications.append({
            "application_id": app.application_id, "req_id": req.req_id, "candidate_id": app.candidate_id,
            "source": app.source, "applied_date": seen[0][1].date(), "current_stage": seen[-1][0],
            "stage_updated_at": seen[-1][1], "hired_employee_id": hired_emp,
        })
        stages += [{"stage_event_id": f"{app.application_id}-{n}", "application_id": app.application_id,
                    "stage": stage, "entered_at": ts} for n, (stage, ts) in enumerate(seen, start=1)]
    return {"ats_applications": applications, "ats_application_stages": stages}
