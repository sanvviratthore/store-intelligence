# PROMPT: Write pytest tests for a retail store event ingestion pipeline. Cover:
# POST /events/ingest happy path, idempotency by event_id, batch of 500 events,
# empty batch, malformed event_type rejected with 422, staff events accepted but
# flagged, re-entry event not double-counting visitor in funnel,
# partial batch with one bad event, all event types in catalogue accepted,
# confidence value boundaries (0.0 and 1.0 both valid).
#
# CHANGES MADE:
# - AI generated only happy path and idempotency — I added all event type coverage
# - Added boundary test for confidence=0.0 (low conf events must not be dropped)
# - Fixed batch size test — AI used 501 events expecting rejection, but limit is
#   enforced by Pydantic max_length=500, so 501 returns 422 not 200
# - Added test verifying X-Trace-Id header present on every response

import os
import tempfile
import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_pipeline.db")
os.environ["POS_PATH"] = os.path.join(tempfile.gettempdir(), "test_pipeline_pos.csv")

with open(os.path.join(tempfile.gettempdir(), "test_pipeline_pos.csv"), "w") as f:
    f.write("store_id,transaction_id,timestamp,basket_value_inr\n")

from app.main import app
from app.database import init_db

init_db()
client = TestClient(app)
NOW = datetime.now(timezone.utc)
STORE = "ST_PIPELINE_TEST"


def make_event(event_type="ENTRY", visitor_id=None, zone_id=None,
               is_staff=False, confidence=0.85, dwell_ms=0, queue_depth=None):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": event_type,
        "timestamp": NOW.isoformat(),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {"queue_depth": queue_depth, "sku_zone": None, "session_seq": 1}
    }


def test_ingest_happy_path():
    events = [make_event("ENTRY") for _ in range(10)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    assert r.json()["accepted"] == 10
    assert r.json()["rejected"] == 0


def test_ingest_idempotent():
    events = [make_event("ENTRY")]
    r1 = client.post("/events/ingest", json={"events": events})
    r2 = client.post("/events/ingest", json={"events": events})
    assert r1.json()["accepted"] == 1
    assert r2.json()["duplicate"] == 1
    assert r2.json()["accepted"] == 0


def test_ingest_empty_batch():
    r = client.post("/events/ingest", json={"events": []})
    assert r.status_code == 200
    assert r.json()["accepted"] == 0


def test_ingest_batch_500():
    events = [make_event("ZONE_ENTER", zone_id="SKINCARE") for _ in range(500)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    assert r.json()["accepted"] == 500


def test_ingest_batch_over_500_rejected():
    events = [make_event("ENTRY") for _ in range(501)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 422


def test_ingest_invalid_event_type():
    bad = make_event("INVALID_TYPE")
    r = client.post("/events/ingest", json={"events": [bad]})
    assert r.status_code == 422


def test_ingest_all_event_types():
    """All valid event types must be accepted."""
    event_types = [
        ("ENTRY", None), ("EXIT", None),
        ("ZONE_ENTER", "SKINCARE"), ("ZONE_EXIT", "SKINCARE"),
        ("ZONE_DWELL", "MAKEUP"), ("BILLING_QUEUE_JOIN", "BILLING"),
        ("BILLING_QUEUE_ABANDON", "BILLING"), ("REENTRY", None),
    ]
    events = [make_event(et, zone_id=zid) for et, zid in event_types]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    assert r.json()["accepted"] == len(event_types)


def test_ingest_low_confidence_not_dropped():
    """confidence=0.0 is valid — low conf events must be stored, not dropped."""
    e = make_event("ENTRY", confidence=0.0)
    r = client.post("/events/ingest", json={"events": [e]})
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_ingest_confidence_boundary_max():
    e = make_event("ENTRY", confidence=1.0)
    r = client.post("/events/ingest", json={"events": [e]})
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_ingest_staff_event_stored():
    """Staff events must be accepted — excluded at query time, not ingest."""
    e = make_event("ZONE_ENTER", zone_id="SKINCARE", is_staff=True)
    r = client.post("/events/ingest", json={"events": [e]})
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_trace_id_header_present():
    r = client.get("/health")
    assert "x-trace-id" in r.headers


def test_reentry_not_double_counted_in_funnel():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        {**make_event("ENTRY", visitor_id=vid), "store_id": store},
        {**make_event("EXIT", visitor_id=vid), "store_id": store},
        {**make_event("REENTRY", visitor_id=vid), "store_id": store},
    ]
    client.post("/events/ingest", json={"events": events})
    funnel = client.get(f"/stores/{store}/funnel").json()["funnel"]
    entry_stage = next(s for s in funnel if s["stage"] == "Entry")
    assert entry_stage["visitors"] == 1


def test_funnel_structure_complete():
    r = client.get(f"/stores/{STORE}/funnel")
    assert r.status_code == 200
    stages = [s["stage"] for s in r.json()["funnel"]]
    assert stages == ["Entry", "Zone Visit", "Billing Queue", "Purchase"]


def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("healthy", "degraded")
    assert "total_events_ingested" in body
    assert "timestamp" in body