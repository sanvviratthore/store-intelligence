import csv
import os
import logging
from datetime import datetime, timedelta, timezone
from app.database import get_conn

logger = logging.getLogger(__name__)
POS_PATH = os.environ.get("POS_PATH", "/data/pos_transactions.csv")


def get_metrics(store_id: str) -> dict:
    conn = get_conn()

    # Unique customer visitors today (exclude staff, count unique visitor_ids with ENTRY)
    unique_visitors = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) as cnt FROM events
        WHERE store_id = ? AND is_staff = 0 AND event_type = 'ENTRY'
    """, (store_id,)).fetchone()["cnt"]

    # Avg dwell per zone
    zone_dwell = conn.execute("""
        SELECT zone_id, AVG(dwell_ms) as avg_dwell_ms, COUNT(*) as visits
        FROM events
        WHERE store_id = ? AND is_staff = 0
          AND event_type IN ('ZONE_DWELL', 'ZONE_EXIT')
          AND zone_id IS NOT NULL
        GROUP BY zone_id
    """, (store_id,)).fetchall()

    avg_dwell_per_zone = {
        row["zone_id"]: {
            "avg_dwell_ms": round(row["avg_dwell_ms"] or 0, 1),
            "visits": row["visits"]
        }
        for row in zone_dwell
    }

    # Current queue depth (latest BILLING_QUEUE_JOIN queue_depth)
    queue_row = conn.execute("""
        SELECT queue_depth FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
          AND queue_depth IS NOT NULL
        ORDER BY timestamp DESC LIMIT 1
    """, (store_id,)).fetchone()
    current_queue_depth = queue_row["queue_depth"] if queue_row else 0

    # Abandonment rate
    total_joins = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN' AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    total_abandons = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_ABANDON' AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    abandonment_rate = round(total_abandons / total_joins, 4) if total_joins > 0 else 0.0

    # Conversion rate via POS correlation
    conversion_rate = _compute_conversion_rate(store_id, conn)

    return {
        "store_id": store_id,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "unique_visitors": unique_visitors,
        "conversion_rate": conversion_rate,
        "avg_dwell_per_zone_ms": avg_dwell_per_zone,
        "current_queue_depth": current_queue_depth,
        "abandonment_rate": abandonment_rate,
    }


def _compute_conversion_rate(store_id: str, conn) -> float:
    """
    Correlate POS transactions with visitor billing zone presence.
    A visitor counts as converted if they were in BILLING zone within
    5 minutes before a transaction timestamp.
    """
    if not os.path.exists(POS_PATH):
        return 0.0

    transactions = []
    try:
        with open(POS_PATH) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["store_id"] == store_id:
                    transactions.append(datetime.fromisoformat(
                        row["timestamp"].replace("Z", "+00:00")
                    ))
    except Exception as e:
        logger.warning(f"POS read error: {e}")
        return 0.0

    if not transactions:
        return 0.0

    # Get all visitors who were in billing zone with timestamps
    billing_visits = conn.execute("""
        SELECT DISTINCT visitor_id, timestamp FROM events
        WHERE store_id = ? AND zone_id = 'BILLING'
          AND is_staff = 0
    """, (store_id,)).fetchall()

    if not billing_visits:
        return 0.0

    converted = set()
    for visit in billing_visits:
        visit_ts = datetime.fromisoformat(visit["timestamp"])
        if visit_ts.tzinfo is None:
            visit_ts = visit_ts.replace(tzinfo=timezone.utc)
        for txn_ts in transactions:
            # Visitor in billing zone within 5 min before transaction
            if timedelta(0) <= (txn_ts - visit_ts) <= timedelta(minutes=5):
                converted.add(visit["visitor_id"])
                break

    total_visitors = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND is_staff = 0 AND event_type = 'ENTRY'
    """, (store_id,)).fetchone()[0]

    if total_visitors == 0:
        return 0.0

    return round(min(len(converted) / total_visitors, 1.0), 4)
