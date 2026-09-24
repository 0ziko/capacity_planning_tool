"""Operasyon onculukleri, gecis kurallari ve haftalik yerlestirme kisitlari.

Haftalik model: wait_minutes=0 iken oncul esigine ulasilan haftada ardil planlanabilir.
Pozitif wait_minutes: oncul tetik haftasinin Pazar 23:59:59 + bekleme -> ardilin alt sinir haftasi.
Kesin gunluk cizelge degildir; UI'da yaklasik haftalik yerlesim olarak gosterilmelidir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.models import Item, RoutingOperation
from app.services import scenarios as scen
from app.services import capacity as cap

BlockReason = Literal["kapasite_yetersiz", "oncul_eksik", "bekleme_ufuk_disinda", "rota_dongusu", "yarimamul_eksik", "operasyon_suresi_eksik"]

WEEKLY_PLANNING_NOTE = (
    "Haftalik planlama yaklasiktir: ayni hafta icindeki operasyon sirasi gun bazinda cozulmez; "
    "bekleme suresi tetik haftasinin Pazar 23:59:59 uzerine eklenerek sonraki haftaya yuvarlanir."
)


@dataclass
class OpDependency:
    pred_op_id: int
    pred_required_qty: float
    ratio: float = 1.0
    kind: str = "sequence"


@dataclass
class ConstraintGraph:
    ops_by_id: dict[int, RoutingOperation]
    required_qty: dict[int, float]
    deps: dict[int, list[OpDependency]]
    errors: list[str] = field(default_factory=list)


class RouteCycleError(ValueError):
    def __init__(self, message: str, *, ops: list[str] | None = None):
        super().__init__(message)
        self.ops = ops or []


def build_sequential_graph(
    ops: list[RoutingOperation],
    qty_by_op: dict[int, float],
) -> ConstraintGraph:
    sorted_ops = sorted(ops, key=lambda o: o.seq)
    ops_by_id = {o.id: o for o in sorted_ops}
    deps: dict[int, list[OpDependency]] = {o.id: [] for o in sorted_ops}
    for i in range(1, len(sorted_ops)):
        pred, succ = sorted_ops[i - 1], sorted_ops[i]
        deps[succ.id].append(
            OpDependency(
                pred_op_id=pred.id,
                pred_required_qty=qty_by_op.get(pred.id, 0.0),
                ratio=1.0,
                kind="sequence",
            )
        )
    errors = detect_cycle(sorted_ops, deps)
    return ConstraintGraph(
        ops_by_id=ops_by_id,
        required_qty={o.id: qty_by_op.get(o.id, 0.0) for o in sorted_ops},
        deps=deps,
        errors=errors,
    )


def add_assembly_dependencies(
    graph: ConstraintGraph,
    finish_ops: list[RoutingOperation],
    wip_last_ops: list[tuple[RoutingOperation, float, str]],
    finish_qty: float,
) -> None:
    """wip_last_ops: (son op, wip gerekli miktar, yarimamul kodu)."""
    if not finish_ops or not wip_last_ops:
        return
    first_finish = min(finish_ops, key=lambda o: o.seq)
    for wip_op, wip_req, _code in wip_last_ops:
        graph.deps.setdefault(first_finish.id, []).append(
            OpDependency(
                pred_op_id=wip_op.id,
                pred_required_qty=wip_req,
                ratio=(wip_req / finish_qty) if finish_qty > 1e-6 else 1.0,
                kind="assembly",
            )
        )
        graph.ops_by_id[wip_op.id] = wip_op
        graph.required_qty.setdefault(wip_op.id, wip_req)


def detect_cycle(ops: list[RoutingOperation], deps: dict[int, list[OpDependency]]) -> list[str]:
    ids = {o.id for o in ops}
    for op_id, edges in deps.items():
        ids.add(op_id)
        ids.update(e.pred_op_id for e in edges)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {i: WHITE for i in ids}
    stack: list[int] = []
    cycle_nodes: list[int] = []

    def dfs(n: int) -> bool:
        color[n] = GRAY
        stack.append(n)
        for e in deps.get(n, []):
            p = e.pred_op_id
            if color.get(p, WHITE) == GRAY:
                cycle_nodes.extend(stack[stack.index(p) :] + [p])
                return True
            if color.get(p, WHITE) == WHITE and dfs(p):
                return True
        stack.pop()
        color[n] = BLACK
        return False

    for nid in list(ids):
        if color.get(nid, WHITE) == WHITE and dfs(nid):
            codes = []
            for oid in cycle_nodes:
                op = next((o for o in ops if o.id == oid), None)
                codes.append(op.operation_name if op else str(oid))
            return [f"rota_dongusu: {' -> '.join(codes)}"]
    return []


def week_end_sunday(week_start: date) -> datetime:
    ws = cap.week_start(week_start)
    return datetime.combine(ws + timedelta(days=6), time(23, 59, 59))


def earliest_week_index_after_wait(trigger_week_idx: int, wait_minutes: float, weeks: list[date]) -> int | None:
    """Tetik haftasinin Pazar 23:59:59 + bekleme -> ardilin ilk izinli hafta indeksi."""
    if trigger_week_idx < 0 or trigger_week_idx >= len(weeks):
        return None
    if wait_minutes <= 0:
        return trigger_week_idx
    earliest = week_end_sunday(weeks[trigger_week_idx]) + timedelta(minutes=wait_minutes)
    for i, wk in enumerate(weeks):
        ws = cap.week_start(wk)
        week_end_exclusive = datetime.combine(ws + timedelta(days=7), time.min)
        if earliest < week_end_exclusive:
            return i
    return None


def cumulative_by_week(cum_by_week: dict[int, float], through_week_idx: int) -> float:
    return sum(q for w, q in cum_by_week.items() if w <= through_week_idx)


def trigger_week_index(
    rule: scen.Rule,
    pred_required: float,
    pred_cum_by_week: dict[int, float],
    weeks: list[date],
) -> int | None:
    running = 0.0
    for i in range(len(weeks)):
        running += pred_cum_by_week.get(i, 0.0)
        if rule.rule == "cycles":
            if running + 1e-6 >= rule.lag_cycles:
                return i
        else:
            if pred_required <= 1e-6 or running + 1e-6 >= pred_required:
                return i
    return None


def transition_min_week_index(
    rule: scen.Rule,
    pred_required: float,
    pred_cum_by_week: dict[int, float],
    weeks: list[date],
) -> tuple[int, BlockReason | None]:
    trigger = trigger_week_index(rule, pred_required, pred_cum_by_week, weeks)
    if trigger is None:
        return len(weeks), "oncul_eksik"
    if rule.wait_minutes > 0:
        ew = earliest_week_index_after_wait(trigger, rule.wait_minutes, weeks)
        if ew is None:
            return len(weeks), "bekleme_ufuk_disinda"
        return ew, None
    return trigger, None


def max_successor_qty(
    rule: scen.Rule,
    pred_required: float,
    pred_available: float,
    succ_required: float,
    succ_planned: float,
) -> float:
    """Ardil icin kalan planlanabilir miktar (kapasite oncesi)."""
    if pred_required <= 1e-6:
        return max(succ_required - succ_planned, 0.0)
    if rule.rule == "finish":
        if pred_available + 1e-6 < pred_required:
            return 0.0
        return max(succ_required - succ_planned, 0.0)
    if pred_available + 1e-6 < rule.lag_cycles:
        return 0.0
    # lag_cycles is a startup threshold, not stock withheld from consumption.
    allowed_total = min(succ_required, max(pred_available, 0.0))
    return max(allowed_total - succ_planned, 0.0)


def assembly_cap_qty(
    assembly_outputs: dict[str, float],
    wip_requirements: list[tuple[str, float]],
    finish_required: float,
) -> float:
    if not wip_requirements:
        return finish_required
    caps: list[float] = []
    for code, wip_req in wip_requirements:
        avail = assembly_outputs.get(code, 0.0)
        ratio = wip_req / finish_required if finish_required > 1e-6 else 1.0
        caps.append(avail / ratio if ratio > 1e-6 else 0.0)
    return min(caps) if caps else finish_required


def predecessor_available(
    pred_op_id: int,
    completed_by_op: dict[int, float],
    cum_planned: dict[int, float],
) -> float:
    return float(completed_by_op.get(pred_op_id, 0.0)) + float(cum_planned.get(pred_op_id, 0.0))


def resolve_min_start_for_op(
    op: RoutingOperation,
    route_item: Item,
    prev_op: RoutingOperation | None,
    rules: scen.RuleLookup | None,
    pred_required: float,
    pred_cum_by_week: dict[int, float],
    weeks: list[date],
    caller_min_start: int,
) -> tuple[int, BlockReason | None]:
    if prev_op is None:
        return caller_min_start, None
    rule = rules.get(route_item, prev_op, op) if rules else scen.Rule()
    tw, reason = transition_min_week_index(rule, pred_required, pred_cum_by_week, weeks)
    return max(caller_min_start, tw), reason


def leadtime_earliest_datetime(
    rule: scen.Rule,
    pred_required: float,
    pred_start: datetime,
    pred_end: datetime,
    quantity: float,
) -> datetime:
    """lead_time ile ayni kural tanimi; tarih/saat sonucu varsa beklemeyi buna ekler."""
    if rule.rule == "cycles":
        frac = min(rule.lag_cycles / quantity, 1.0) if quantity > 1e-6 else 0.0
        earliest = pred_start + (pred_end - pred_start) * frac
    else:
        earliest = pred_end
    if rule.wait_minutes > 0:
        earliest += timedelta(minutes=rule.wait_minutes)
    return earliest


def unplanned_entry(
    *,
    order_no: str,
    item_code: str,
    operation_seq: int,
    work_center_code: str,
    hours: float,
    reason: BlockReason,
    semi_finished_code: str = "",
    weekly_note: bool = True,
) -> dict:
    row = {
        "order_no": order_no,
        "item_code": item_code,
        "semi_finished_code": semi_finished_code,
        "operation_seq": operation_seq,
        "work_center_code": work_center_code,
        "hours": round(hours, 8),
        "reason": reason,
    }
    if weekly_note:
        row["planning_granularity"] = "weekly_approx"
    return row
