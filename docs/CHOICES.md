# CHOICES.md — Architectural Decision Log

## Decision 1: Detection Model — YOLOv8n

### Options Considered
| Model | Pros | Cons |
|-------|------|------|
| YOLOv8n | Fastest, CPU-friendly, Ultralytics ecosystem includes ByteTrack | Lower accuracy than larger variants |
| YOLOv8m | Better accuracy, handles occlusion better | ~4× slower on CPU — unacceptable for 5 clips |
| RT-DETR | Transformer-based, better at groups | No built-in tracker, much heavier |
| MediaPipe Pose | Lightweight | Not designed for tracking, no person Re-ID |
| GPT-4V / Gemini Vision | Could handle staff detection, zone classification | Per-frame API cost, latency ~2s/frame, offline dependency |

### What AI Suggested
Claude suggested YOLOv8m as a balance between speed and accuracy, and also floated GPT-4V for zone classification because the footage has clear brand signage that a VLM could read. I evaluated the VLM route: the latency was prohibitive for real-time use and the cost model doesn't fit an offline challenge submission.

### What I Chose and Why
**YOLOv8n** with ByteTrack (built into Ultralytics). Reasons:
1. CPU-only constraint — no GPU available on the submission machine
2. The footage is 1080p at 30fps but only ~2.3 min per clip; YOLOv8n at every 5th frame processes ~840 frames per clip in under 5 minutes on CPU
3. Person detection (class 0) at 1080p with retail-level crowd density is well within YOLOv8n's capability — the challenging cases (groups, occlusion) are handled by ByteTrack's IoU-based association rather than the detector
4. The Ultralytics package bundles ByteTrack — one dependency, one install

**Trade-off acknowledged**: YOLOv8n will miss some partially-occluded persons. The system handles this by emitting low-confidence events rather than dropping them (confidence is stored and surfaced in the API).

---

## Decision 2: Event Schema Design

### Options Considered
- **Option A**: Flat schema, one row per detection (every frame)
- **Option B**: Session-level aggregation (one record per visit)
- **Option C**: Typed event stream (the chosen approach) — discrete events at meaningful moments

### What AI Suggested
Claude suggested Option B (session-level aggregation) as simpler to query. I disagreed: aggregating at ingest time loses the raw signal needed for anomaly detection. If I aggregate immediately, I can't retroactively compute queue depth at a specific timestamp or detect abandonment (which requires knowing the time between BILLING_QUEUE_JOIN and EXIT with no POS correlation).

### What I Chose and Why
**Typed event stream (Option C)**, matching the required schema exactly. Each event carries:
- `event_id` (UUIDv4): globally unique, primary key for idempotency
- `visitor_id`: per-session Re-ID token, reused on REENTRY
- `event_type`: from the defined catalogue (ENTRY, EXIT, ZONE_ENTER, etc.)
- `timestamp`: derived from clip base timestamp + frame offset, not wall-clock time
- `is_staff`: Boolean flag — stored on raw events, filtered at query time (not at ingest)
- `confidence`: never suppressed, even for low-confidence detections — this lets the API surface data quality signals

**Key design principle**: Staff events are stored with `is_staff=true` but not excluded at ingest. This means the raw events table is a complete audit trail. Exclusion happens at the SQL query layer in every metric computation. This makes the system easier to audit and allows re-classification if the staff detection model improves.

---

## Decision 3: API Storage — SQLite vs PostgreSQL

### Options Considered
| Option | Pros | Cons |
|--------|------|------|
| SQLite | Zero external deps, `docker compose up` trivially satisfied, sufficient for clip volume | Not suitable for 40-store concurrent writes at production scale |
| PostgreSQL | Production-grade, concurrent writes, better query planner | Requires separate container, adds complexity, overkill for challenge scope |
| Redis + PostgreSQL | Real-time queue depth from Redis, analytics from Postgres | Two external services, significant operational complexity |

### What AI Suggested
Claude recommended PostgreSQL from the start, citing concurrent write safety. It was right about the production argument. However, it also noted that for a challenge submission where the acceptance gate requires `docker compose up` with no manual steps, SQLite removes an entire failure mode (Postgres container failing to start, volume permissions, connection strings).

### What I Chose and Why
**SQLite** for the submission, with a clear migration path documented.

The event volume from 5 × 2.3-minute clips is at most ~10,000 events. SQLite handles this trivially. The acceptance gate is harder to fail with SQLite. The API is structured so that replacing the SQLite connection in `database.py` with a PostgreSQL connection string is a single-file change — all queries use standard SQL with no SQLite-specific syntax.

**What breaks at scale (honest answer)**: At 40 live stores sending events in real-time, the first thing that breaks is SQLite's single-writer lock. Concurrent ingest from multiple pipeline instances would serialize and create a backlog. The fix is PostgreSQL with connection pooling (pgBouncer) and a time-series partitioned events table by `(store_id, day)`. Metric queries would move to pre-aggregated materialized views refreshed every 60 seconds.
