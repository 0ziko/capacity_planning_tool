"""Planlama simulasyonu taslak satiri — planning ve co_shipment ortak."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models import Order


@dataclass
class DraftLine:
    order: Order
    order_id: int
    operation_id: int
    work_center_id: int
    week_start: date
    planned_hours: float
    planned_qty: float
    mode: str = "auto"
    production_batch_id: int | None = None
    label: str = ""
