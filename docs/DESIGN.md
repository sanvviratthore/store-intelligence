# DESIGN.md — Purplle Store Intelligence System

## Architecture Overview

The system is a four-stage pipeline that converts raw CCTV footage into queryable store analytics:

```
CCTV Clips (CAM_*.mp4)
       ↓
Detection Layer (YOLOv8n + ByteTrack)
       ↓
Event Stream (JSONL file → HTTP batch ingest)
       ↓
Intelligence API (FastAPI + SQLite)
       ↓
Live Dashboard (terminal / web)
```

### Stage 1 — Detection Layer (`pipeline/`)

Each video clip is processed by `detect.py`, which runs YOLOv8n with ByteTrack tracking at every 5th frame (configurable via `--process-every`) for CPU performance. Each detected person is assigned a persistent `track_id` by ByteTrack across frames within a clip.

The `VisitorTracker` class handles:
- **Entry/exit direction**: On CAM_ENTRY_01, the frame is split at 55% height. A person whose Y-centroid moves from below the line to above it is classified as ENTRY; the reverse is EXIT.
- **Staff classification**: Any person who remains in frame for >120 seconds with lateral movement covering less than 30% of frame width is flagged `is_staff=True`. CAM_STOCK_01 (stockroom) flags all detections as staff automatically.
- **Re-ID**: When a track disappears and a new detection appears within 30 seconds at a similar position (within 200px), the original `visitor_id` is reused and a REENTRY event is emitted instead of a new ENTRY.
- **Zone dwell**: ZONE_DWELL events are emitted every 30 seconds of continuous presence in a named zone. ZONE_ENTER and ZONE_EXIT bracket each visit.

### Stage 2 — Event Schema

Events are emitted as newline-delimited JSON (JSONL) and batch-ingested into the API via POST `/events/ingest`. The schema matches the required specification exactly, with `event_id` (UUIDv4), ISO-8601 UTC timestamps derived from frame index + clip base timestamp, and a `metadata` block carrying `queue_depth`, `sku_zone`, and `session_seq`.

### Stage 3 — Intelligence API (`app/`)

Built with FastAPI and SQLite. All metrics are computed on-read from the raw events table — no pre-aggregation cache — which keeps the system simple and correct for the clip durations involved. At 40-store scale with high event volume, this would need a time-series store and pre-aggregated materialized views (see follow-up question notes in CHOICES.md).

**Conversion rate** is computed by correlating `BILLING` zone visit timestamps with POS transaction timestamps: a visitor is counted as converted if they were in the BILLING zone within the 5-minute window before a transaction. This avoids requiring a `customer_id` in the POS data.

**Idempotency** is enforced via `event_id` as a SQLite PRIMARY KEY. Duplicate ingest calls return `duplicate` count without error.

**Graceful degradation**: If the database is unavailable, endpoints return HTTP 503 with a structured JSON body. No raw stack traces are exposed.

### Stage 4 — Dashboard

A terminal dashboard (`dashboard/live.py`) polls `/stores/{id}/metrics` and `/stores/{id}/anomalies` every 3 seconds and renders a live updating view using the `rich` library.

---

## Camera Role Mapping

| File     | camera_id       | Role        | Notes |
|----------|-----------------|-------------|-------|
| CAM_3.mp4 | CAM_ENTRY_01  | entry_exit  | Glass door threshold, Purplle signage |
| CAM_1.mp4 | CAM_FLOOR_01  | main_floor  | Skincare + suncare zone |
| CAM_2.mp4 | CAM_FLOOR_02  | main_floor  | Makeup + lips/eyes zone |
| CAM_5.mp4 | CAM_BILLING_01| billing     | POS counter with laptop visible |
| CAM_4.mp4 | CAM_STOCK_01  | stockroom   | Backroom — all persons = staff |

Camera roles were determined by visual inspection of footage frames, not by filename.

---

## AI-Assisted Decisions

### 1. Entry/exit direction heuristic (accepted with modification)

I asked Claude to suggest how to determine entry vs exit direction without a calibration step. It suggested using optical flow to detect dominant motion direction across the entry zone. I evaluated this but found it fragile when multiple people move in different directions simultaneously (group entry case). I overrode this with a simpler per-track Y-centroid trajectory approach: track the last 8 positions and check if the person crossed the entry line in a consistent direction. This is more robust for the edge cases in the footage.

### 2. Staff classification approach (partially accepted)

Claude suggested using a VLM (GPT-4V or Gemini Vision) to classify staff by uniform colour. I tested this on sample frames and found it added significant latency (~2s per frame) and was inconsistent due to the blurred faces making context harder to read. I replaced it with a behavioural heuristic: long dwell + limited lateral movement. This is faster, runs offline, and is defensible in the follow-up questions since I can explain exactly what the threshold values mean.

### 3. Database choice (accepted)

When I asked about storage for the analytics queries, Claude recommended PostgreSQL for production and SQLite for the challenge. I accepted SQLite — the reasoning being that the event volume from 5 short clips fits comfortably in SQLite, and the acceptance gate requirement (`docker compose up` with no external dependencies) is much easier to satisfy without a separate Postgres container. CHOICES.md documents the trade-offs explicitly.
