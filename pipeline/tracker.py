"""
tracker.py — Per-camera visitor tracking, Re-ID, entry/exit direction,
staff classification, zone dwell tracking, and event generation.
"""

import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Entry camera: bottom half of frame = outside, top half = inside
# Person moving from bottom→top = ENTRY; top→bottom = EXIT
ENTRY_LINE_Y_RATIO = 0.55  # Configurable per camera if needed

# Staff heuristic: person who stays in frame for >80% of clip duration
# and rarely moves laterally (behind counter)
STAFF_DWELL_THRESHOLD_SEC = 120  # 2+ minutes in frame = likely staff
STAFF_ZONE_RATIO = 0.3           # Occupies <30% of frame width movement

# Re-ID window: if same approximate position reappears within this many seconds
# after an EXIT, treat as REENTRY rather than new ENTRY
REENTRY_WINDOW_SEC = 30
REENTRY_POSITION_TOLERANCE = 200  # pixels

ZONE_DWELL_EMIT_INTERVAL_SEC = 30


class TrackedPerson:
    def __init__(self, track_id: int, visitor_id: str, first_seen: datetime,
                 first_bbox: tuple, is_staff: bool = False):
        self.track_id = track_id
        self.visitor_id = visitor_id
        self.first_seen = first_seen
        self.last_seen = first_seen
        self.first_bbox = first_bbox
        self.last_bbox = first_bbox
        self.is_staff = is_staff
        self.current_zone: Optional[str] = None
        self.zone_enter_time: Optional[datetime] = None
        self.last_dwell_emit: Optional[datetime] = None
        self.session_seq = 0
        self.positions: list[tuple] = [self._center(first_bbox)]
        self.has_entered = False   # Crossed entry threshold inbound
        self.has_exited = False
        self.entry_y_history: list[float] = []

    def _center(self, bbox):
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    def update(self, bbox: tuple, ts: datetime):
        self.last_seen = ts
        self.last_bbox = bbox
        cx, cy = self._center(bbox)
        self.positions.append((cx, cy))
        if len(self.positions) > 60:
            self.positions = self.positions[-60:]

    def lateral_range(self) -> float:
        if len(self.positions) < 2:
            return 0
        xs = [p[0] for p in self.positions]
        return max(xs) - min(xs)

    def dwell_seconds(self, now: datetime) -> float:
        return (now - self.first_seen).total_seconds()

    def next_seq(self) -> int:
        self.session_seq += 1
        return self.session_seq


class VisitorTracker:
    def __init__(self, camera_id: str, role: str):
        self.camera_id = camera_id
        self.role = role
        self.active: dict[int, TrackedPerson] = {}        # track_id → TrackedPerson
        self.visitor_map: dict[int, str] = {}             # track_id → visitor_id
        self.exited: list[dict] = []                      # recently exited persons for Re-ID

    def update(self, detections: list[dict], timestamp: datetime,
               zone_clf, frame, frame_idx: int) -> list[dict]:
        events = []
        seen_track_ids = set()

        frame_h, frame_w = frame.shape[:2]

        for det in detections:
            tid = det["track_id"]
            bbox = det["bbox"]
            conf = det["confidence"]
            cx, cy = det["center"]
            seen_track_ids.add(tid)

            if tid not in self.active:
                # New track — check if Re-ID matches a recent exit
                visitor_id, is_reentry = self._resolve_visitor_id(cx, cy, timestamp)
                person = TrackedPerson(
                    track_id=tid,
                    visitor_id=visitor_id,
                    first_seen=timestamp,
                    first_bbox=bbox,
                )
                self.active[tid] = person
                self.visitor_map[tid] = visitor_id

                if self.role == "stockroom":
                    person.is_staff = True

                if self.role == "entry_exit":
                    # Don't emit ENTRY immediately — wait for direction confirmation
                    pass
                elif is_reentry:
                    events.append(self._make_event(person, "REENTRY", timestamp, conf))
                    person.has_entered = True
                else:
                    if self.role != "entry_exit":
                        # On floor/billing cameras, appearing = already inside
                        person.has_entered = True

            person = self.active[tid]
            person.update(bbox, timestamp)

            # Staff classification: long dwell + limited lateral movement
            if (not person.is_staff and
                    person.dwell_seconds(timestamp) > STAFF_DWELL_THRESHOLD_SEC and
                    person.lateral_range() < frame_w * STAFF_ZONE_RATIO):
                person.is_staff = True
                logger.debug(f"Classified track {tid} as staff")

            # Entry/exit direction logic (entry camera only)
            if self.role == "entry_exit":
                entry_line_y = frame_h * ENTRY_LINE_Y_RATIO
                person.entry_y_history.append(cy)

                if len(person.entry_y_history) >= 8 and not person.has_entered and not person.has_exited:
                    recent = person.entry_y_history[-8:]
                    # Moving upward (decreasing y) = entering store
                    if recent[0] > entry_line_y and recent[-1] < entry_line_y:
                        person.has_entered = True
                        events.append(self._make_event(person, "ENTRY", timestamp, conf))
                    # Moving downward (increasing y) = exiting store
                    elif recent[0] < entry_line_y and recent[-1] > entry_line_y:
                        person.has_exited = True
                        events.append(self._make_event(person, "EXIT", timestamp, conf))

            # Zone tracking (floor and billing cameras)
            if self.role in ("main_floor", "billing"):
                zone = zone_clf.classify(cx, cy)
                if zone != person.current_zone:
                    if person.current_zone is not None:
                        # Emit ZONE_EXIT for previous zone
                        dwell = int((timestamp - person.zone_enter_time).total_seconds() * 1000)
                        events.append(self._make_event(
                            person, "ZONE_EXIT", timestamp, conf,
                            zone_id=person.current_zone, dwell_ms=dwell
                        ))
                    # Emit ZONE_ENTER for new zone
                    if zone:
                        events.append(self._make_event(
                            person, "ZONE_ENTER", timestamp, conf, zone_id=zone
                        ))
                    person.current_zone = zone
                    person.zone_enter_time = timestamp
                    person.last_dwell_emit = timestamp

                # Emit ZONE_DWELL every 30s of continuous presence
                if (person.current_zone and person.zone_enter_time and
                        person.last_dwell_emit and
                        (timestamp - person.last_dwell_emit).total_seconds() >= ZONE_DWELL_EMIT_INTERVAL_SEC):
                    dwell = int((timestamp - person.zone_enter_time).total_seconds() * 1000)
                    events.append(self._make_event(
                        person, "ZONE_DWELL", timestamp, conf,
                        zone_id=person.current_zone, dwell_ms=dwell
                    ))
                    person.last_dwell_emit = timestamp

                # Billing queue logic
                if zone == "BILLING" and self.role == "billing":
                    queue_depth = sum(
                        1 for p in self.active.values()
                        if p.current_zone == "BILLING" and not p.is_staff
                    )
                    if queue_depth > 1 and person.current_zone != "BILLING":
                        events.append(self._make_event(
                            person, "BILLING_QUEUE_JOIN", timestamp, conf,
                            zone_id="BILLING",
                            metadata={"queue_depth": queue_depth}
                        ))

        # Handle lost tracks (person left frame)
        lost_ids = set(self.active.keys()) - seen_track_ids
        for tid in lost_ids:
            person = self.active.pop(tid)
            cx, cy = person._center(person.last_bbox)

            # Record exit position for Re-ID
            self.exited.append({
                "visitor_id": person.visitor_id,
                "cx": cx, "cy": cy,
                "exited_at": timestamp,
                "is_staff": person.is_staff,
            })
            # Prune old exits
            self.exited = [
                e for e in self.exited
                if (timestamp - e["exited_at"]).total_seconds() < REENTRY_WINDOW_SEC
            ]

            # Emit billing queue abandon if left billing without purchase
            if person.current_zone == "BILLING" and self.role == "billing" and not person.is_staff:
                events.append(self._make_event(person, "BILLING_QUEUE_ABANDON", timestamp, 0.7))

        return events

    def flush(self, timestamp: datetime) -> list[dict]:
        """Close all open sessions at end of clip."""
        events = []
        for tid, person in list(self.active.items()):
            if person.current_zone and self.role in ("main_floor", "billing"):
                dwell = int((timestamp - (person.zone_enter_time or person.first_seen)).total_seconds() * 1000)
                events.append(self._make_event(
                    person, "ZONE_EXIT", timestamp, 0.7,
                    zone_id=person.current_zone, dwell_ms=dwell
                ))
        self.active.clear()
        return events

    def _resolve_visitor_id(self, cx: float, cy: float, ts: datetime):
        """Check if this new detection matches a recently exited visitor (Re-ID)."""
        for exit_record in self.exited:
            age = (ts - exit_record["exited_at"]).total_seconds()
            if age > REENTRY_WINDOW_SEC:
                continue
            dist = ((cx - exit_record["cx"]) ** 2 + (cy - exit_record["cy"]) ** 2) ** 0.5
            if dist < REENTRY_POSITION_TOLERANCE:
                return exit_record["visitor_id"], True
        return f"VIS_{uuid.uuid4().hex[:6]}", False

    def _make_event(self, person: TrackedPerson, event_type: str,
                    ts: datetime, conf: float,
                    zone_id: str = None, dwell_ms: int = 0,
                    metadata: dict = None) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "visitor_id": person.visitor_id,
            "event_type": event_type,
            "timestamp": ts.isoformat(),
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": person.is_staff,
            "confidence": round(conf, 3),
            "metadata": {
                "queue_depth": (metadata or {}).get("queue_depth"),
                "sku_zone": None,
                "session_seq": person.next_seq(),
            }
        }
