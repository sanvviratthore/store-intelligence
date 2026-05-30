from app.database import get_conn


def get_funnel(store_id: str) -> dict:
    conn = get_conn()

    # Count unique visitor sessions at each funnel stage
    # Session = unique visitor_id (re-entries don't double count)
    entries = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'ENTRY' AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    zone_visitors = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type IN ('ZONE_ENTER','ZONE_DWELL')
          AND is_staff = 0 AND zone_id NOT IN ('ENTRY','BILLING','STOCKROOM')
    """, (store_id,)).fetchone()[0]

    billing_visitors = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND zone_id = 'BILLING' AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    # Purchases = visitors who joined billing queue and didn't abandon
    purchases = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN' AND is_staff = 0
          AND visitor_id NOT IN (
              SELECT DISTINCT visitor_id FROM events
              WHERE store_id = ? AND event_type = 'BILLING_QUEUE_ABANDON'
          )
    """, (store_id, store_id)).fetchone()[0]

    def drop(a, b):
        if a == 0:
            return 0.0
        return round((a - b) / a * 100, 2)

    return {
        "store_id": store_id,
        "funnel": [
            {
                "stage": "Entry",
                "visitors": entries,
                "drop_off_pct": 0.0
            },
            {
                "stage": "Zone Visit",
                "visitors": zone_visitors,
                "drop_off_pct": drop(entries, zone_visitors)
            },
            {
                "stage": "Billing Queue",
                "visitors": billing_visitors,
                "drop_off_pct": drop(zone_visitors, billing_visitors)
            },
            {
                "stage": "Purchase",
                "visitors": purchases,
                "drop_off_pct": drop(billing_visitors, purchases)
            }
        ]
    }
