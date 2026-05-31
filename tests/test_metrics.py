# PROMPT: Write pytest tests for the /metrics and /heatmap endpoints of a retail
# store analytics FastAPI app. Cover: unique visitor count excluding staff,
# conversion rate with POS correlation, zero-traffic store handling,
# avg dwell per zone, queue depth, abandonment rate, heatmap normalisation,
# data_confidence flag when sessions < 20. Use isolated SQLite DB per test module.
#
# CHANGES MADE:
# - Added explicit timezone handling (AI used naive datetimes initially)
# - Replaced hardcoded store IDs with unique per-test stores to avoid cross-test pollution
# - Added assertion that conversion_rate <= 1.0 (AI missed the cap)
# - Added test for heatmap returning zones sorted by normalised_score descending

import os
import tempfile
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_metrics.db")
os.environ["POS_PATH"] = os.path.join(tempfile.gettempdir(), "test_metrics_pos.csv")

with open(os.path.join(tempfile.gettempdir(), "test_metrics_pos.csv"), "w") as f:
    f.write("store_id,transaction_id,timestamp,basket_value_inr\n")

from app.main import app
from app.database import init_db

init_db()
client = TestClient(app)
NOW = datetime.now(timezone.utc)


def make_event(store_id, event_type="ENTRY", visitor_id=None, is_staff=False,
               zone_id=None, dwell_ms=0, queue_depth=None, confidence=0.85):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
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


def test_metrics_unique_visitors_excludes_staff():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    customer = make_event(store, "ENTRY", is_staff=False)
    staff = make_event(store, "ENTRY", is_staff=True)
    client.post("/events/ingest", json={"events": [customer, staff]})
    r = client.get(f"/stores/{store}/metrics")
    assert r.json()["unique_visitors"] == 1


def test_metrics_zero_traffic():
    r = client.get(f"/stores/STORE_EMPTY_{uuid.uuid4().hex[:4]}/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["unique_visitors"] == 0
    assert body["conversion_rate"] == 0.0
    assert body["current_queue_depth"] == 0
    assert body["abandonment_rate"] == 0.0


def test_metrics_conversion_rate_capped_at_1():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    # Ingest more billing visitors than total entries (edge case)
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    entry = make_event(store, "ENTRY", visitor_id=vid)
    billing = make_event(store, "ZONE_ENTER", visitor_id=vid, zone_id="BILLING")
    # Add POS transaction
    txn_ts = (NOW + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(tempfile.gettempdir(), "test_metrics_pos.csv"), "a") as f:
        f.write(f"{store},TXN_{uuid.uuid4().hex[:8]},{txn_ts},500.00\n")
    client.post("/events/ingest", json={"events": [entry, billing]})
    r = client.get(f"/stores/{store}/metrics")
    assert r.json()["conversion_rate"] <= 1.0


def test_metrics_avg_dwell_per_zone():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    e = make_event(store, "ZONE_EXIT", zone_id="SKINCARE", dwell_ms=15000)
    client.post("/events/ingest", json={"events": [e]})
    r = client.get(f"/stores/{store}/metrics")
    zones = r.json()["avg_dwell_per_zone_ms"]
    assert "SKINCARE" in zones
    assert zones["SKINCARE"]["avg_dwell_ms"] == 15000.0


def test_metrics_abandonment_rate():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    join = make_event(store, "BILLING_QUEUE_JOIN", zone_id="BILLING", queue_depth=2)
    abandon = make_event(store, "BILLING_QUEUE_ABANDON",
                         visitor_id=join["visitor_id"])
    client.post("/events/ingest", json={"events": [join, abandon]})
    r = client.get(f"/stores/{store}/metrics")
    assert r.json()["abandonment_rate"] == 1.0


def test_heatmap_normalised_score_range():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    events = [make_event(store, "ZONE_ENTER", zone_id=z)
              for z in ["SKINCARE", "MAKEUP", "BILLING", "LIPS_EYES"]]
    client.post("/events/ingest", json={"events": events})
    r = client.get(f"/stores/{store}/heatmap")
    for zone in r.json()["zones"]:
        assert 0 <= zone["normalised_score"] <= 100


def test_heatmap_sorted_descending():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    # BILLING gets 3 visits, SKINCARE gets 1
    events = [make_event(store, "ZONE_ENTER", zone_id="BILLING") for _ in range(3)]
    events += [make_event(store, "ZONE_ENTER", zone_id="SKINCARE")]
    client.post("/events/ingest", json={"events": events})
    zones = client.get(f"/stores/{store}/heatmap").json()["zones"]
    scores = [z["normalised_score"] for z in zones]
    assert scores == sorted(scores, reverse=True)


def test_heatmap_low_confidence_flag():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    # Only 5 sessions — below 20 threshold
    events = [make_event(store, "ENTRY") for _ in range(5)]
    client.post("/events/ingest", json={"events": events})
    r = client.get(f"/stores/{store}/heatmap")
    assert r.json()["data_confidence"] == "LOW"


def test_acceptance_gate_store_blr_002():
    """Acceptance gate: STORE_BLR_002 alias must return valid metrics."""
    r = client.get("/stores/STORE_BLR_002/metrics")
    assert r.status_code == 200
    assert "unique_visitors" in r.json()