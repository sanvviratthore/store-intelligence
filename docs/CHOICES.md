# CHOICES.md — Architectural Decision Log

## Decision 1: Detection Model — YOLOv8n

### Options I Considered

| Model | Why I considered it | Why I didn't use it |
|-------|---------------------|---------------------|
| YOLOv8n | Fastest, CPU-friendly, ByteTrack built in | Lower accuracy than larger variants |
| YOLOv8m | Better occlusion handling | ~4x slower on CPU — 2.3min clip would take 30+ min |
| RT-DETR | Transformer-based, better group detection | No built-in tracker, much heavier |
| MediaPipe | Very lightweight | Not designed for tracking, no persistent IDs |
| GPT-4V / Gemini Vision | Could read zone signage, classify staff by context | 2s per frame latency, offline requirement fails |

### What AI Suggested

Claude suggested YOLOv8m as a balance between speed and accuracy, and also suggested GPT-4V for zone classification since the footage has clear brand signage that a VLM could read. I tested the VLM idea — for zone classification it actually made sense since CAM_1 and CAM_2 clearly show brand names on the shelves. But the latency was prohibitive for a CPU-only machine processing 15,000+ frames.

### What I Chose and Why

**YOLOv8n** with ByteTrack. My machine has no GPU. At every 5th frame, YOLOv8n processes roughly 840 frames per 2.3-minute clip in about 8 minutes on CPU. YOLOv8m would have taken over 30 minutes per clip — not practical for a submission deadline.

The trade-off I accepted: YOLOv8n will miss some partially-occluded persons. I handled this by storing confidence scores on every event and surfacing data_confidence: LOW in the heatmap when sessions are below 20. Low confidence events are stored, not dropped — the reviewer can see the confidence distribution in the raw events.

**One thing I would change:** if I had a GPU, I would use YOLOv8m and add a proper OSNet Re-ID model instead of the position-based approach. The current Re-ID breaks when two people of similar build enter from the same direction within 30 seconds of each other.

---

## Decision 2: Event Schema Design

### Options I Considered

- **Option A — Flat schema, one row per detection frame:** Maximum raw data, but storage explodes (30fps × 5 clips × 2.3 min = ~20,000 rows for a single clip) and querying session-level metrics becomes expensive.
- **Option B — Session-level aggregation at ingest:** One record per visit session. Simpler queries but destroys the raw signal — can't retroactively compute queue depth at a specific timestamp or detect abandonment.
- **Option C — Typed event stream:** Discrete events at meaningful moments (ENTRY, ZONE_ENTER, ZONE_DWELL, etc.). This is what I chose.

### What AI Suggested

Claude initially suggested Option B (session aggregation) as simpler to query. I disagreed. If I aggregate at ingest, I lose the ability to detect BILLING_QUEUE_ABANDON — which requires knowing the time gap between a BILLING_QUEUE_JOIN and an EXIT without a following POS transaction. You can only compute that from the raw event sequence, not from a pre-aggregated session record.

### What I Chose and Why

**Typed event stream (Option C).** Key design principles I followed:

1. **Staff flagged, not excluded at ingest.** is_staff=true events are stored. Every metric query filters them out in SQL. This means if my staff classifier makes a mistake, I can rerun metrics without reprocessing the video.

2. **Confidence never suppressed.** A detection with confidence=0.15 still gets stored. The system degrades gracefully — low confidence events contribute to the heatmap with a LOW confidence flag rather than being silently dropped. This is honest about what the model actually saw.

3. **event_id as UUIDv4 primary key.** Makes ingest idempotent by design. The pipeline can be rerun against the same clips without duplicating events.

4. **Timestamps from video OSD, not wall clock.** The clips have an embedded timestamp (10/04/2026 20:09). I use this as the base and add frame offset. This means events from different camera clips have coherent timestamps even when processed hours apart.

---

## Decision 3: API Storage — SQLite vs PostgreSQL

### Options I Considered

| Option | Pros | Cons |
|--------|------|------|
| SQLite | Zero dependencies, docker compose up works instantly | Single-writer lock, not suitable for concurrent production writes |
| PostgreSQL | Production-grade, concurrent writes, better query planner | Extra container, startup race conditions, volume permissions on Windows |
| Redis + PostgreSQL | Real-time queue depth from Redis, analytics from Postgres | Two external services, major complexity increase |

### What AI Suggested

Claude recommended PostgreSQL from the start, citing concurrent write safety and better indexing for time-series queries. It was right about the production argument.

### What I Chose and Why

**SQLite** for this submission, with a documented migration path.

The event volume from 5 clips (618 events) fits trivially in SQLite. More importantly, the acceptance gate requires docker compose up with zero manual steps. On Windows specifically (which is my development machine), getting a Postgres container to start cleanly with the right volume permissions is a known pain point. SQLite eliminates that entire failure mode.

**What breaks at scale (honest answer):** At 40 live stores sending events concurrently, the first thing that breaks is SQLite's global write lock. Two pipeline instances trying to ingest simultaneously will serialize and create a backlog. The fix is PostgreSQL with a partitioned events table by (store_id, date) and pre-aggregated materialized views refreshed every 60 seconds for the metrics endpoints. The current code is structured so this migration is a single-file change in database.py — all queries use standard SQL with no SQLite-specific syntax.

**Why I didn't add Postgres anyway:** Adding complexity I can't test properly under deadline pressure is worse than a known limitation I can explain clearly. A system that works simply is better than a system that almost works with unnecessary complexity.