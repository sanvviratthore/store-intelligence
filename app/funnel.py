from app.database import get_conn
from app.metrics import _compute_conversion_rate


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
        WHERE store_id = ? AND (zone_id = 'BILLING' OR camera_id = 'CAM_BILLING_01')
          AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    # Purchases = use same POS correlation as metrics endpoint
    # so funnel Purchase count is consistent with conversion_rate
    conversion_rate = _compute_conversion_rate(store_id, conn)
    purchases = round(conversion_rate * entries) if entries > 0 else 0

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