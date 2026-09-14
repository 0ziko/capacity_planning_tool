"""Malzeme hazirlik durumu — haftalik plan baslangic siniri (Faz 07; gunluk motor Faz 10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.models import Order, ProductionBatch
from app.services import capacity as cap

MATERIAL_READY = "ready"
MATERIAL_EXPECTED = "expected"
MATERIAL_UNKNOWN = "unknown"

POLICY_CONDITIONAL = "conditional"
POLICY_STRICT = "strict"


@dataclass(frozen=True)
class MaterialGate:
    status: str
    earliest_week: date | None
    week_rounding_note: str
    blocks_planning: bool
    conditional: bool
    material_unverified: bool


def material_earliest_week(ready_date: date) -> tuple[date, str]:
    """Pazartesi ise ayni hafta; diger gunlerde sonraki haftadan (konservatif)."""
    ws = cap.week_start(ready_date)
    if ready_date == ws:
        return ws, "Malzeme tarihi hafta basi (Pazartesi); ayni hafta planlanabilir."
    nxt = ws + timedelta(weeks=1)
    return nxt, f"Malzeme {ready_date.isoformat()} hafta ortasi/sonu; plan {nxt.isoformat()} haftasindan."


def material_gate_for_order(order: Order, *, policy: str = POLICY_CONDITIONAL) -> MaterialGate:
    st = (getattr(order, "material_status", None) or MATERIAL_UNKNOWN).strip() or MATERIAL_UNKNOWN
    if st == MATERIAL_READY:
        return MaterialGate(
            status=st,
            earliest_week=None,
            week_rounding_note="",
            blocks_planning=False,
            conditional=False,
            material_unverified=False,
        )
    if st == MATERIAL_EXPECTED:
        rd = getattr(order, "material_ready_date", None)
        if not rd:
            return MaterialGate(
                status=st,
                earliest_week=None,
                week_rounding_note="expected durumunda material_ready_date zorunlu",
                blocks_planning=True,
                conditional=False,
                material_unverified=False,
            )
        ew, note = material_earliest_week(rd)
        return MaterialGate(
            status=st,
            earliest_week=ew,
            week_rounding_note=note,
            blocks_planning=False,
            conditional=False,
            material_unverified=False,
        )
    # unknown
    if policy == POLICY_STRICT:
        return MaterialGate(
            status=st,
            earliest_week=None,
            week_rounding_note="",
            blocks_planning=True,
            conditional=False,
            material_unverified=False,
        )
    return MaterialGate(
        status=st,
        earliest_week=None,
        week_rounding_note="",
        blocks_planning=False,
        conditional=True,
        material_unverified=True,
    )


def material_gate_for_batch(batch: ProductionBatch, *, policy: str = POLICY_CONDITIONAL) -> MaterialGate:
    """Parti: en kisitlayici malzeme durumu (unknown > expected > ready)."""
    orders = [l.order for l in batch.orders if l.order]
    if not orders:
        return material_gate_for_order(
            type("_", (), {"material_status": MATERIAL_UNKNOWN, "material_ready_date": None})(),
            policy=policy,
        )
    gates = [material_gate_for_order(o, policy=policy) for o in orders]
    if any(g.blocks_planning for g in gates):
        blocked = next(g for g in gates if g.blocks_planning)
        return blocked
    earliest: date | None = None
    notes: list[str] = []
    unverified = False
    status = MATERIAL_READY
    for g in gates:
        if g.material_unverified:
            unverified = True
            status = MATERIAL_UNKNOWN
        elif g.status == MATERIAL_EXPECTED:
            if status != MATERIAL_UNKNOWN:
                status = MATERIAL_EXPECTED
            if g.earliest_week and (earliest is None or g.earliest_week > earliest):
                earliest = g.earliest_week
                notes.append(g.week_rounding_note)
    note = notes[0] if notes else ""
    if unverified:
        return MaterialGate(
            status=MATERIAL_UNKNOWN,
            earliest_week=earliest,
            week_rounding_note=note,
            blocks_planning=False,
            conditional=True,
            material_unverified=True,
        )
    return MaterialGate(
        status=status,
        earliest_week=earliest,
        week_rounding_note=note,
        blocks_planning=False,
        conditional=False,
        material_unverified=False,
    )


def week_index(weeks: list[date], week_start: date) -> int:
    for i, wk in enumerate(weeks):
        if wk >= week_start:
            return i
    return len(weeks)
