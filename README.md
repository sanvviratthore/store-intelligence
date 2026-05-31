# Purplle Store Intelligence System

End-to-end pipeline from raw CCTV footage to live store analytics API.

## Quick Start (5 commands)

```bash
# 1. Clone the repo
git clone <your-repo-url> && cd store-intelligence

# 2. Start the API
docker compose up --build -d

# 3. Verify it's running
curl http://localhost:8000/health

# 4. Install detection pipeline dependencies (run locally, not in Docker)
pip install -r pipeline/requirements.txt

# 5. Run the detection pipeline against your clips
python -m pipeline.detect --clips-dir ./clips --output ./data/events.jsonl --api-url http://localhost:8000
```

After step 5, the API is live with real data from your clips.

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py       # Main detection + tracking script (YOLOv8n + ByteTrack)
│   ├── tracker.py      # Re-ID, entry/exit direction, staff classification
│   ├── zones.py        # Zone classifier from store_layout.json
│   ├── emit.py         # JSONL writer + API ingest client
│   └── requirements.txt
├── app/
│   ├── main.py         # FastAPI entrypoint + middleware
│   ├── models.py       # Pydantic event schema
│   ├── database.py     # SQLite init and connection
│   ├── ingestion.py    # Ingest + dedup logic
│   ├── metrics.py      # Real-time metrics + POS correlation
│   ├── funnel.py       # Conversion funnel
│   ├── heatmap.py      # Zone heatmap (normalised 0–100)
│   ├── anomalies.py    # Anomaly detection engine
│   ├── health.py       # Health + stale feed detection
│   └── requirements.txt
├── data/
│   ├── store_layout.json
│   └── pos_transactions.csv
├── tests/
│   └── test_api.py
├── docs/
│   ├── DESIGN.md
│   └── CHOICES.md
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/events/ingest` | Batch ingest up to 500 events. Idempotent by event_id |
| GET | `/stores/{id}/metrics` | Unique visitors, conversion rate, dwell, queue, abandonment |
| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase funnel with drop-off % |
| GET | `/stores/{id}/heatmap` | Zone frequency + dwell, normalised 0–100 |
| GET | `/stores/{id}/anomalies` | Active anomalies (queue spike, dead zone, conversion drop) |
| GET | `/health` | Service status, last event per store, STALE_FEED warning |

### Example: Ingest an event

```bash
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "event_id": "550e8400-e29b-41d4-a716-446655440000",
      "store_id": "ST1008",
      "camera_id": "CAM_ENTRY_01",
      "visitor_id": "VIS_abc123",
      "event_type": "ENTRY",
      "timestamp": "2026-04-10T14:40:00Z",
      "zone_id": null,
      "dwell_ms": 0,
      "is_staff": false,
      "confidence": 0.92,
      "metadata": {"queue_depth": null, "sku_zone": null, "session_seq": 1}
    }]
  }'
```

### Example: Get store metrics

```bash
curl http://localhost:8000/stores/ST1008/metrics
```

---

## Running Tests

```bash
pip install -r app/requirements.txt pytest
pytest tests/ -v --tb=short
```

---

## Detection Pipeline Options

```bash
python -m pipeline.detect \
  --clips-dir ./clips \           # Directory with CAM_*.mp4 files
  --layout ./data/store_layout.json \
  --output ./data/events.jsonl \  # Output events file
  --api-url http://localhost:8000 \ # Optional: stream to API live
  --process-every 5               # Process every 5th frame (CPU speed)
```

**Expected clip filenames:**
- `CAM_1.mp4` → Main floor (skincare zone)
- `CAM_2.mp4` → Main floor (makeup zone)
- `CAM_3.mp4` → Entry/exit threshold
- `CAM_4.mp4` → Stockroom (staff only)
- `CAM_5.mp4` → Billing counter

---

## Store ID

The single store in the dataset is: `ST1008`

---

## Architecture Notes

- **No GPU required** — YOLOv8n runs on CPU. Processing all 5 clips takes ~15–20 minutes.
- **Idempotent ingest** — Safe to run the detection pipeline multiple times; duplicate `event_id`s are silently skipped.
- **Staff exclusion** — Staff events are stored with `is_staff=true` and excluded at query time (not at ingest), preserving the full audit trail.
- See `docs/DESIGN.md` for full architecture and `docs/CHOICES.md` for decision rationale.

## Live Dashboard

Web dashboard available at: **http://localhost:8000**

Auto-refreshes every 5 seconds showing:
- Live visitor count and conversion rate
- Conversion funnel with drop-off %
- Zone heatmap normalised 0-100
- Active anomalies with severity and suggested actions
- System health and feed lag

Terminal dashboard (alternative):
```bash
pip install rich
python -m dashboard.live
```

## One-Command Pipeline

```bash
bash pipeline/run.sh ./clips http://localhost:8000
```
