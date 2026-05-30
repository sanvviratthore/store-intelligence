from datetime import datetime, timedelta, timezone
from app.database import get_conn


def get_health() -> dict:
    now = datetime.now(timezone.utc)
    try:
        conn = get_conn()
        stores = conn.execute("""
            SELECT store_id, MAX(timestamp) as last_event
            FROM events GROUP BY store_id
        """).fetchall()

        store_status = []
        for row in stores:
            last_ts = datetime.fromisoformat(row["last_event"])
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            lag = (now - last_ts).total_seconds()
            store_status.append({
                "store_id": row["store_id"],
                "last_event_timestamp": row["last_event"],
                "lag_seconds": round(lag, 1),
                "status": "STALE_FEED" if lag > 600 else "OK"
            })

        total_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        return {
            "status": "healthy",
            "timestamp": now.isoformat(),
            "total_events_ingested": total_events,
            "stores": store_status,
            "db": "connected"
        }
    except Exception as e:
        return {
            "status": "degraded",
            "timestamp": now.isoformat(),
            "error": str(e),
            "db": "unavailable"
        }
