# DESIGN.md — Purplle Store Intelligence System

## Architecture Overview

The system is a four-stage pipeline that converts raw CCTV footage into queryable store analytics:

```
CCTV Clips (CAM_1 to CAM_5)
       ↓
Detection Layer (YOLOv8n + ByteTrack)   [pipeline/]
       ↓
Event Stream (JSONL → HTTP batch ingest)
       ↓
Intelligence API (FastAPI + SQLite)      [app/]
       ↓
Live Dashboard (Web UI at localhost:8000 + terminal)
```

---

## Stage 1 — Camera Role Assignment

The first thing I did before writing any detection code was manually inspect frames from each clip. The camera roles are not labelled in the filenames — I had to figure them out from the footage itself:

- **CAM_3** → Entry/exit camera. Shows a glass partition door with a Purplle sunscreen poster. The threshold is narrow and the camera angle is top-down at roughly 45 degrees. This is where ENTRY and EXIT events come from.
- **CAM_1** → Main floor, skincare side. Shows the left wall shelves (EB Korean, The Face Shop, Good Vibes, DermDoc, Minimalist, Aqualogica) and a central circular display stand.
- **CAM_2** → Main floor, makeup side. Shows the right wall (Maybelline, Faces Canada, Lakme, Swiss Beauty, Renee NY Bae, Alps Goodness) and a makeup trial unit.
- **CAM_5** → Billing counter. A laptop and barcode scanner are clearly visible on the counter — this is the POS terminal. This is where billing zone events come from.
- **CAM_4** → Stockroom. Purplle-branded cardboard boxes stacked on shelves, no customer-facing merchandise. Every person detected here is staff by definition.

This visual inspection shaped the entire pipeline design. CAM_4 detections are all flagged is_staff=true without any ML classification needed.

**Store 2 (ST1076) cameras:**
- **entry_1, entry_2** → Two entry camera angles at the same glass door. Much cleaner threshold view than Store 1 — no poster obstruction. Date: 29/03/2026 and 08/03/2026.
- **zone.mp4** → Main floor zone showing skincare and haircare wall shelves (CAM2).
- **billing_area.mp4** → Overhead billing counter view with POS terminal visible (CAM6). Staff member in pink uniform clearly identifiable.

The pipeline processes both stores sequentially, emitting events tagged with the correct store_id (ST1008 or ST1076).

---

## Stage 2 — Detection and Tracking

**Model:** YOLOv8n with ByteTrack (built into Ultralytics).

Each clip is processed at every 5th frame (configurable via --process-every) to make CPU processing feasible. At 30fps, processing every 5th frame gives effective 6fps analysis which is sufficient for retail foot traffic.

**Entry/exit direction (CAM_3 specific):**

The entry camera shows a narrow glass door. I set the entry line at 55% of frame height. A person's Y-centroid is tracked over 8 consecutive frames — if it moves from below the line to above it, an ENTRY event is emitted. The reverse produces an EXIT.

I originally tried 4 frames but got false positives from people pausing at the door. 8 frames requires more committed movement, which reduced false positives at the cost of some sensitivity. This is why only 2 ENTRY events were detected in the 2.3-minute clip — the footage is short and people visible are mostly already inside rather than crossing the threshold.

**Staff classification:**

Looking at CAM_1, the person in black who stands behind the central circular display for the entire clip is clearly staff. My heuristic: any person who remains in frame for more than 120 seconds AND whose lateral movement covers less than 30% of frame width is classified as staff. This captures the "standing behind counter" behavior without needing uniform detection.

**Re-ID:**

When a tracked person disappears and a new detection appears within 30 seconds at a similar position (within 200 pixels), the original visitor_id is reused and a REENTRY event is emitted. This is position-based Re-ID — lightweight and CPU-friendly, though it can fail if two different people enter from the same direction within the window.

---

## Stage 3 — Event Schema

Events are emitted as JSONL and batch-ingested via POST /events/ingest. Key design decisions:

- **Timestamps** are derived from the clip's embedded OSD timestamp (10/04/2026 20:09) plus frame offset — not wall clock time. This gives accurate relative timing within a session.
- **Staff events are stored, not dropped.** is_staff=true events go into the database. Exclusion happens at query time in every SQL query. This preserves a complete audit trail.
- **Confidence is never suppressed.** Even 0.1 confidence detections are stored. The API surfaces data_confidence: LOW when session count is under 20.

---

## Stage 4 — Intelligence API

FastAPI with SQLite. All metrics computed on-read from raw events — no pre-aggregation. Endpoints:

| Endpoint | Key Logic |
|----------|-----------|
| POST /events/ingest | Idempotent by event_id (PRIMARY KEY). Partial success on bad events. |
| GET /stores/{id}/metrics | Conversion via POS time-window correlation. Staff excluded in SQL. |
| GET /stores/{id}/funnel | Session-level dedup — DISTINCT visitor_id, not raw event count. |
| GET /stores/{id}/heatmap | Normalised 0-100 by dividing each zone visit count by max zone visits. |
| GET /stores/{id}/anomalies | Queue spike (>3), dead zone (no visits in 30 min), conversion drop (<10%). |
| GET /health | STALE_FEED if last event >10 min ago. |

**Conversion rate** is computed by correlating BILLING zone visit timestamps with POS transaction timestamps. A visitor counts as converted if they were in the BILLING zone within 2 hours of a transaction. The wide window accounts for timezone differences between the embedded video timestamp (IST) and the POS data.

**Graceful degradation:** database unavailable returns HTTP 503 with structured JSON, no stack traces.

---

## Stage 5 — Live Dashboard

Two options ship:
1. **Web UI** at http://localhost:8000 — dark-themed dashboard with KPI cards, funnel, heatmap, anomalies, health. Auto-refreshes every 5 seconds. Features include:
   - Store switcher dropdown (ST1008 ↔ ST1076)
   - Visual floor heatmap grid with color intensity (cold blue → hot red)
   - OpenStreetMap with clickable store location pins (no API key needed)
2. **Terminal dashboard** via python -m dashboard.live using the rich library.

Both poll the same API endpoints — proof that the pipeline and API are genuinely connected.

---

## AI-Assisted Decisions

### 1. Entry/exit direction detection (overrode AI suggestion)

I asked Claude to suggest how to determine entry vs exit direction without a camera calibration step. It suggested using optical flow to detect dominant motion direction across the entry zone. I evaluated this against the CAM_3 footage — optical flow would struggle when multiple people move in different directions simultaneously (one entering, one exiting). I overrode this with a per-track Y-centroid trajectory approach: track the last 8 positions and check if the person crossed the 55% height line consistently. Simpler and more robust for the single-door scenario in this footage.

### 2. Staff classification (partially overrode)

Claude suggested using a VLM (GPT-4V or Gemini Vision) to classify staff by uniform colour. I looked at the footage — faces are blurred, and the person in CAM_1 who is clearly staff wears black which customers also wear. Uniform colour alone would not work reliably. I overrode this with a behavioural heuristic (long dwell + limited lateral movement) which is faster, runs offline, and does not require API calls per frame. CAM_4 (stockroom) is an additional hard signal — anyone in that room is staff regardless of model output.

### 3. Database choice (accepted AI recommendation with caveats)

Claude recommended SQLite for the submission and PostgreSQL for production. I accepted this reasoning. The acceptance gate requires docker compose up with no manual steps — adding a Postgres container introduces a failure mode (startup race conditions, volume permissions). SQLite removes that risk entirely for a take-home submission. The migration path to PostgreSQL is a single-file change in database.py since all queries use standard SQL.