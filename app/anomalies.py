from datetime import datetime, timedelta, timezone
from app.database import get_conn


def get_anomalies(store_id: str) -> dict:
    conn = get_conn()
    anomalies = []
    now = datetime.now(timezone.utc)

    # 1. BILLING_QUEUE_SPIKE — queue depth > 3
    queue_row = conn.execute("""
        SELECT queue_depth, timestamp FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
          AND queue_depth IS NOT NULL
        ORDER BY timestamp DESC LIMIT 1
    """, (store_id,)).fetchone()

    if queue_row and queue_row["queue_depth"] and queue_row["queue_depth"] > 3:
        anomalies.append({
            "anomaly_id": "BILLING_QUEUE_SPIKE",
            "severity": "CRITICAL" if queue_row["queue_depth"] > 5 else "WARN",
            "description": f"Queue depth is {queue_row['queue_depth']} at billing counter",
            "detected_at": queue_row["timestamp"],
            "suggested_action": "Open additional billing counter or redirect customers"
        })

    # 2. DEAD_ZONE — any zone with no visits in last 30 minutes
    known_zones = ["SKINCARE", "SUNCARE", "MAKEUP", "LIPS_EYES"]
    cutoff = (now - timedelta(minutes=30)).isoformat()
    for zone in known_zones:
        recent = conn.execute("""
            SELECT COUNT(*) as cnt FROM events
            WHERE store_id = ? AND zone_id = ? AND timestamp > ? AND is_staff = 0
        """, (store_id, zone, cutoff)).fetchone()["cnt"]

        # Only flag if we have some data but zone went quiet
        total_zone = conn.execute("""
            SELECT COUNT(*) as cnt FROM events
            WHERE store_id = ? AND zone_id = ? AND is_staff = 0
        """, (store_id, zone)).fetchone()["cnt"]

        if total_zone > 0 and recent == 0:
            anomalies.append({
                "anomaly_id": f"DEAD_ZONE_{zone}",
                "severity": "INFO",
                "description": f"No customer visits in {zone} zone for 30+ minutes",
                "detected_at": now.isoformat(),
                "suggested_action": f"Check {zone} zone display and signage"
            })

    # 3. CONVERSION_DROP — abandonment rate > 40%
    total_joins = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN' AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    total_abandons = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_ABANDON' AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    if total_joins > 0:
        abandon_rate = total_abandons / total_joins
        if abandon_rate > 0.4:
            anomalies.append({
                "anomaly_id": "HIGH_ABANDONMENT_RATE",
                "severity": "WARN",
                "description": f"Billing queue abandonment rate is {abandon_rate:.0%}",
                "detected_at": now.isoformat(),
                "suggested_action": "Investigate queue wait time; consider staff reallocation"
            })

    # 4. LOW_CONVERSION — fewer than 10% visitors purchasing
    total_visitors = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'ENTRY' AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    purchases = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN' AND is_staff = 0
          AND visitor_id NOT IN (
              SELECT DISTINCT visitor_id FROM events
              WHERE store_id = ? AND event_type = 'BILLING_QUEUE_ABANDON'
          )
    """, (store_id, store_id)).fetchone()[0]

    if total_visitors >= 5:
        conversion = purchases / total_visitors
        if conversion < 0.10:
            anomalies.append({
                "anomaly_id": "CONVERSION_DROP",
                "severity": "WARN",
                "description": f"Conversion rate is {conversion:.1%} — below 10% threshold",
                "detected_at": now.isoformat(),
                "suggested_action": "Review pricing, promotions, and staff engagement"
            })

    return {
        "store_id": store_id,
        "checked_at": now.isoformat(),
        "active_anomalies": anomalies,
        "anomaly_count": len(anomalies)
    }
