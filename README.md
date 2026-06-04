# Purplle Store Intelligence System

End-to-end pipeline from raw CCTV footage to live store analytics API.

## Quick Start (5 commands)

```bash
# 1. Clone the repo
git clone https://github.com/sanvviratthore/store-intelligence && cd store-intelligence

# 2. Add video clips (download from HackerEarth challenge page)
mkdir clips
# Copy all downloaded video files into the clips/ folder (see clip names below)

# 3. Start the API
docker compose up --build -d

# 4. Install detection pipeline dependencies (run locally, not in Docker)
pip install -r pipeline/requirements.txt

# 5. Run the detection pipeline against your clips
python -m pipeline.detect --clips-dir ./clips --output ./data/events.jsonl --api-url http://localhost:8000
```

> **Note:** Video clips are not included in this repo per challenge rules. Download them from the HackerEarth challenge page and place in `./clips/`. The API works without clips — `docker compose up` starts everything and all endpoints respond immediately.

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py        # Main detection + tracking script (YOLOv8n + ByteTrack)
│   ├── tracker.py       # Re-ID, entry/exit direction, staff classification
│   ├── zones.py         # Zone classifier from store_layout.json
│   ├── emit.py          # JSONL writer + API ingest client
│   ├── run.sh           # One-command pipeline runner
│   └── requirements.txt
├── app/
│   ├── main.py          # FastAPI entrypoint + middleware
│   ├── models.py        # Pydantic event schema
│   ├── database.py      # SQLite init and connection
│   ├── ingestion.py     # Ingest + dedup logic
│   ├── metrics.py       # Real-time metrics + POS correlation
│   ├── funnel.py        # Conversion funnel
│   ├── heatmap.py       # Zone heatmap (normalised 0-100)
│   ├── anomalies.py     # Anomaly detection engine
│   ├── health.py        # Health + stale feed detection
│   ├── static/
│   │   └── index.html   # Live web dashboard
│   └── requirements.txt
├── data/
│   ├── store_layout.json      # Zone definitions for both stores
│   ├── pos_transactions.csv   # POS transaction records
│   └── sample_events.jsonl    # Reference event schema examples
├── tests/
│   ├── test_api.py
│   ├── test_pipeline.py
│   ├── test_metrics.py
│   └── test_anomalies.py
├── docs/
│   ├── DESIGN.md        # Architecture + AI-assisted decisions
│   └── CHOICES.md       # 3 decisions with full reasoning
├── dashboard/
│   └── live.py          # Terminal dashboard (rich)
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
| GET | `/stores/{id}/heatmap` | Zone frequency + dwell, normalised 0-100 |
| GET | `/stores/{id}/anomalies` | Active anomalies (queue spike, dead zone, conversion drop) |
| GET | `/health` | Service status, last event per store, STALE_FEED warning |

---

## Store IDs

Two stores in the dataset:
- **ST1008** — Brigade Road, Bangalore
- **ST1076** — Mumbai store

```bash
http://localhost:8000/stores/ST1008/metrics
http://localhost:8000/stores/ST1076/metrics
http://localhost:8000/stores/STORE_BLR_002/metrics  # acceptance gate alias
```

---

## Expected Clip Filenames (place in ./clips/)

**Store 1 — ST1008 (Brigade Road, Bangalore):**
- `CAM_1_zone.mp4` → Main floor, skincare/suncare wall
- `CAM_2_zone.mp4` → Main floor, makeup/cosmetics wall
- `CAM_3_entry.mp4` → Entry/exit glass door threshold
- `CAM_5_billing.mp4` → Billing counter with POS terminal

**Store 2 — ST1076:**
- `entry_1.mp4` → Primary entry camera
- `entry_2.mp4` → Secondary entry camera
- `zone.mp4` → Main floor zone
- `billing_area.mp4` → Billing counter area

---

## Running Tests

```bash
pip install pytest httpx
python -m pytest tests/ -v
```

49 tests covering: idempotency, staff exclusion, re-entry dedup, anomaly detection, funnel accuracy, zero-traffic handling.

---

## Detection Pipeline Options

```bash
python -m pipeline.detect \
  --clips-dir ./clips \
  --layout ./data/store_layout.json \
  --output ./data/events.jsonl \
  --api-url http://localhost:8000 \
  --process-every 5
```

Or use the one-command runner:
```bash
bash pipeline/run.sh ./clips http://localhost:8000
```

---

## Live Dashboard

Web dashboard at: **http://localhost:8000**

Features:
- Store switcher (ST1008 ↔ ST1076)
- Live KPI cards: visitors, conversion rate, queue depth, abandonment
- Conversion funnel with drop-off percentages
- Zone heatmap table (normalised 0-100) + visual floor grid
- Active anomalies with severity and suggested actions
- System health with STALE_FEED detection
- OpenStreetMap with clickable store location pins

Terminal dashboard:
```bash
pip install rich
python -m dashboard.live
```

---

## Architecture Notes

- **No GPU required** — YOLOv8n runs on CPU. ~15-20 min to process all clips.
- **Idempotent ingest** — duplicate `event_id`s are silently skipped, safe to rerun.
- **Staff excluded at query time** — `is_staff=true` events stored but filtered in SQL.
- **Two-store support** — ST1008 and ST1076 processed in a single pipeline run.
- **Synthetic ENTRY events** — visitors detected on floor cameras but not entry camera get inferred ENTRY events (confidence 0.60) to handle obstructed entry views.

See `docs/DESIGN.md` for architecture decisions and `docs/CHOICES.md` for trade-off reasoning.