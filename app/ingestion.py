import sqlite3
import logging
from datetime import datetime, timezone
from app.models import StoreEvent, IngestRequest, IngestResponse
from app.database import get_conn

logger = logging.getLogger(__name__)


def ingest_events(request: IngestRequest) -> IngestResponse:
    conn = get_conn()
    accepted = 0
    rejected = 0
    duplicate = 0
    errors = []

    for idx, event in enumerate(request.events):
        try:
            _insert_event(conn, event)
            accepted += 1
        except sqlite3.IntegrityError:
            # Idempotent: duplicate event_id is silently skipped
            duplicate += 1
        except Exception as e:
            rejected += 1
            errors.append({"index": idx, "event_id": event.event_id, "error": str(e)})
            logger.warning(f"Event rejected: {event.event_id} — {e}")

    conn.commit()
    return IngestResponse(accepted=accepted, rejected=rejected, duplicate=duplicate, errors=errors)


def _insert_event(conn: sqlite3.Connection, event: StoreEvent):
    conn.execute("""
        INSERT INTO events (
            event_id, store_id, camera_id, visitor_id, event_type,
            timestamp, zone_id, dwell_ms, is_staff, confidence,
            queue_depth, sku_zone, session_seq
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event.event_id,
        event.store_id,
        event.camera_id,
        event.visitor_id,
        event.event_type,
        event.timestamp.isoformat(),
        event.zone_id,
        event.dwell_ms,
        1 if event.is_staff else 0,
        event.confidence,
        event.metadata.queue_depth,
        event.metadata.sku_zone,
        event.metadata.session_seq,
    ))
