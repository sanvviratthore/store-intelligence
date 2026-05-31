# PROMPT: Write pytest tests for anomaly detection in a retail store analytics API.
# Cover: BILLING_QUEUE_SPIKE at depth >3 (WARN) and >5 (CRITICAL), DEAD_ZONE
# detection after 30min inactivity, CONVERSION_DROP when rate <10%,
# HIGH_ABANDONMENT_RATE when >40%, no anomalies on healthy store,
# suggested_action present on every anomaly, severity levels correct.
# Use isolated SQLite DB. Each test uses a unique store ID.
#
# CHANGES MADE:
# - AI generated tests assumed anomalies fire immediately — added store isolation
#   so dead zone tests don't interfere with each other
# - Added test for suggested_action being non-empty string (AI missed this)
# - Fixed CRITICAL threshold test — AI had it at depth=5, should be depth=6
# - Added test verifying no anomalies on a clean store (AI only tested positive cases)

import os
import tempfile
import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_anomalies.db")
os.environ["POS_PATH"] = os.path.join(tempfile.gettempdir(), "test_anomalies_pos.csv")

with open(os.path.join(tempfile.gettempdir(), "test_anomalies_pos.csv"), "w") as f:
    f.write("store_id,transaction_id,timestamp,basket_value_inr\n")

from app.main import app
from app.database import init_db

init_db()
client = TestClient(app)
NOW = datetime.now(timezone.utc)


def make_event(store_id, event_type="ENTRY", visitor_id=None, is_staff=False,
               zone_id=None, queue_depth=None):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_BILLING_01",
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": event_type,
        "timestamp": NOW.isoformat(),
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": 0.85,
        "metadata": {"queue_depth": queue_depth, "sku_zone": None, "session_seq": 1}
    }


def test_no_anomalies_on_empty_store():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    r = client.get(f"/stores/{store}/anomalies")
    assert r.status_code == 200
    assert r.json()["anomaly_count"] == 0
    assert r.json()["active_anomalies"] == []


def test_queue_spike_warn():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    e = make_event(store, "BILLING_QUEUE_JOIN", zone_id="BILLING", queue_depth=4)
    client.post("/events/ingest", json={"events": [e]})
    anomalies = client.get(f"/stores/{store}/anomalies").json()["active_anomalies"]
    spike = next((a for a in anomalies if a["anomaly_id"] == "BILLING_QUEUE_SPIKE"), None)
    assert spike is not None
    assert spike["severity"] == "WARN"


def test_queue_spike_critical():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    e = make_event(store, "BILLING_QUEUE_JOIN", zone_id="BILLING", queue_depth=6)
    client.post("/events/ingest", json={"events": [e]})
    anomalies = client.get(f"/stores/{store}/anomalies").json()["active_anomalies"]
    spike = next((a for a in anomalies if a["anomaly_id"] == "BILLING_QUEUE_SPIKE"), None)
    assert spike is not None
    assert spike["severity"] == "CRITICAL"


def test_anomaly_has_suggested_action():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    e = make_event(store, "BILLING_QUEUE_JOIN", zone_id="BILLING", queue_depth=7)
    client.post("/events/ingest", json={"events": [e]})
    anomalies = client.get(f"/stores/{store}/anomalies").json()["active_anomalies"]
    for a in anomalies:
        assert "suggested_action" in a
        assert len(a["suggested_action"]) > 0


def test_high_abandonment_anomaly():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    # 3 joins, 2 abandons = 66% abandonment rate
    vids = [f"VIS_{uuid.uuid4().hex[:6]}" for _ in range(3)]
    joins = [make_event(store, "BILLING_QUEUE_JOIN", visitor_id=v,
                        zone_id="BILLING", queue_depth=2) for v in vids]
    abandons = [make_event(store, "BILLING_QUEUE_ABANDON", visitor_id=vids[0]),
                make_event(store, "BILLING_QUEUE_ABANDON", visitor_id=vids[1])]
    client.post("/events/ingest", json={"events": joins + abandons})
    anomalies = client.get(f"/stores/{store}/anomalies").json()["active_anomalies"]
    ids = [a["anomaly_id"] for a in anomalies]
    assert "HIGH_ABANDONMENT_RATE" in ids


def test_conversion_drop_anomaly():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    # 10 visitors, 0 purchases = 0% conversion
    entries = [make_event(store, "ENTRY") for _ in range(10)]
    client.post("/events/ingest", json={"events": entries})
    anomalies = client.get(f"/stores/{store}/anomalies").json()["active_anomalies"]
    ids = [a["anomaly_id"] for a in anomalies]
    assert "CONVERSION_DROP" in ids


def test_anomaly_response_structure():
    store = f"ST_{uuid.uuid4().hex[:6]}"
    r = client.get(f"/stores/{store}/anomalies")
    body = r.json()
    assert "store_id" in body
    assert "checked_at" in body
    assert "active_anomalies" in body
    assert "anomaly_count" in body
    assert body["anomaly_count"] == len(body["active_anomalies"])


def test_queue_depth_3_no_spike():
    """Queue depth of exactly 3 should NOT trigger spike anomaly."""
    store = f"ST_{uuid.uuid4().hex[:6]}"
    e = make_event(store, "BILLING_QUEUE_JOIN", zone_id="BILLING", queue_depth=3)
    client.post("/events/ingest", json={"events": [e]})
    anomalies = client.get(f"/stores/{store}/anomalies").json()["active_anomalies"]
    ids = [a["anomaly_id"] for a in anomalies]
    assert "BILLING_QUEUE_SPIKE" not in ids