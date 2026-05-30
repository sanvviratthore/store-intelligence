from app.database import get_conn


def get_heatmap(store_id: str) -> dict:
    conn = get_conn()

    rows = conn.execute("""
        SELECT zone_id,
               COUNT(DISTINCT visitor_id) as visit_count,
               AVG(dwell_ms) as avg_dwell_ms
        FROM events
        WHERE store_id = ? AND is_staff = 0
          AND zone_id IS NOT NULL
          AND zone_id NOT IN ('STOCKROOM')
        GROUP BY zone_id
    """, (store_id,)).fetchall()

    if not rows:
        return {"store_id": store_id, "zones": [], "data_confidence": "LOW"}

    visit_counts = [r["visit_count"] for r in rows]
    max_visits = max(visit_counts) if visit_counts else 1

    total_sessions = conn.execute("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = ? AND event_type = 'ENTRY' AND is_staff = 0
    """, (store_id,)).fetchone()[0]

    data_confidence = "LOW" if total_sessions < 20 else "HIGH"

    zones = []
    for row in rows:
        normalised = round((row["visit_count"] / max_visits) * 100) if max_visits > 0 else 0
        zones.append({
            "zone_id": row["zone_id"],
            "visit_count": row["visit_count"],
            "avg_dwell_ms": round(row["avg_dwell_ms"] or 0, 1),
            "normalised_score": normalised
        })

    zones.sort(key=lambda z: z["normalised_score"], reverse=True)

    return {
        "store_id": store_id,
        "zones": zones,
        "data_confidence": data_confidence,
        "total_sessions": total_sessions
    }
