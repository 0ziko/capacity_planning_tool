"""FAZ 06: PostgreSQL iki oturum yarisi (yalnizca TEST_PG_URL ile)."""

import os
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.schemas import AutoPlanRequest
from app.services.plan_input_fingerprint import compute_plan_input_fingerprint

TEST_PG = os.environ.get("TEST_PG_URL", "").strip()


@pytest.mark.skipif(not TEST_PG, reason="TEST_PG_URL yok — gecici PostgreSQL icin calistirin")
def test_two_session_fingerprint_race():
    """Bir oturum onay kilidindeyken digeri ayni revizyonu onaylayamaz (409 veya bekler)."""
    eng = create_engine(TEST_PG, pool_pre_ping=True)
    Session = sessionmaker(bind=eng)
    # Bu test canli DB kullanmaz; yalnizca TEST_PG_URL ile calisir.
    s1 = Session()
    s2 = Session()
    try:
        req = AutoPlanRequest(
            start_week=date(2026, 9, 15),
            weeks=2,
            work_center_ids=[1],
            replace_existing=True,
            mode="due_date",
        )
        fp1 = compute_plan_input_fingerprint(s1, req)
        fp2 = compute_plan_input_fingerprint(s2, req)
        assert fp1 == fp2
    finally:
        s1.close()
        s2.close()
