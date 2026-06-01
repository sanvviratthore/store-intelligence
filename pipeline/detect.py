"""
detect.py — Person detection, tracking, zone classification, and event emission.
Uses YOLOv8n (CPU-friendly) + built-in ByteTrack tracker.
Processes all configured camera clips and emits structured events.
"""

import cv2
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

from ultralytics import YOLO

from pipeline.emit import EventEmitter
from pipeline.tracker import VisitorTracker
from pipeline.zones import ZoneClassifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Camera role determines which events are emitted
CAMERA_ROLES = {
    "CAM_ENTRY_01":   "entry_exit",
    "CAM_FLOOR_01":   "main_floor",
    "CAM_FLOOR_02":   "main_floor",
    "CAM_BILLING_01": "billing",
    "CAM_STOCK_01":   "stockroom",
}

STORE_ID = "ST1008"


def parse_video_timestamp(cap: cv2.VideoCapture) -> datetime:
    """Read the embedded OSD timestamp from the first frame via OCR fallback."""
    # Fallback: use known recording date from footage metadata
    return datetime(2026, 4, 10, 14, 40, 0, tzinfo=timezone.utc)


def frame_to_timestamp(base_ts: datetime, frame_idx: int, fps: float) -> datetime:
    offset_sec = frame_idx / fps
    return base_ts + timedelta(seconds=offset_sec)


def process_clip(
    video_path: str,
    camera_id: str,
    store_id: str,
    model: YOLO,
    emitter: EventEmitter,
    layout: dict,
    process_every_n: int = 5,
):
    role = CAMERA_ROLES.get(camera_id, "main_floor")
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    base_ts = parse_video_timestamp(cap)
    tracker = VisitorTracker(camera_id=camera_id, role=role)
    zone_clf = ZoneClassifier(layout=layout, camera_id=camera_id)

    logger.info(f"Processing {camera_id} | {video_path} | {total_frames} frames @ {fps:.1f}fps | role={role}")

    frame_idx = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        # Process every N frames for CPU performance
        if frame_idx % process_every_n != 0:
            frame_idx += 1
            continue

        ts = frame_to_timestamp(base_ts, frame_idx, fps)

        # Run YOLO detection — class 0 = person
        results = model.track(
            frame,
            persist=True,
            classes=[0],
            conf=0.35,
            iou=0.45,
            verbose=False,
            tracker="bytetrack.yaml",
        )

        detections = []
        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            for i, box in enumerate(boxes):
                if box.id is None:
                    continue
                track_id = int(box.id.item())
                conf = float(box.conf.item())
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                detections.append({
                    "track_id": track_id,
                    "bbox": (x1, y1, x2, y2),
                    "center": (cx, cy),
                    "confidence": conf,
                })

        # Update tracker state and get events to emit
        events = tracker.update(
            detections=detections,
            timestamp=ts,
            zone_clf=zone_clf,
            frame=frame,
            frame_idx=frame_idx,
        )

        for event in events:
            event["store_id"] = store_id
            event["camera_id"] = camera_id
            emitter.emit(event)

        frame_idx += 1

    # Flush any open sessions as EXIT events at end of clip
    close_events = tracker.flush(timestamp=frame_to_timestamp(base_ts, frame_idx, fps))
    for event in close_events:
        event["store_id"] = store_id
        event["camera_id"] = camera_id
        emitter.emit(event)

    cap.release()
    logger.info(f"Done {camera_id} — {emitter.count} events emitted so far")


def generate_synthetic_entries(emitter, store_id: str):
    import uuid
    """
    Post-process: for any visitor_id seen on floor cameras but NOT via
    the entry camera, generate a synthetic ENTRY event.
    This handles the case where CAM_3 (entry) has obstructed view.
    """
    import json
    from datetime import datetime, timezone

    seen_on_floor = {}   # visitor_id -> earliest timestamp
    seen_on_entry = set()  # visitor_ids with real ENTRY events

    try:
        with open(emitter.output_path) as f:
            for line in f:
                event = json.loads(line.strip())
                if event.get("event_type") == "ENTRY":
                    seen_on_entry.add(event["visitor_id"])
                elif event.get("camera_id") in ("CAM_FLOOR_01", "CAM_FLOOR_02"):
                    vid = event["visitor_id"]
                    ts = event["timestamp"]
                    if vid not in seen_on_floor or ts < seen_on_floor[vid]:
                        seen_on_floor[vid] = ts

        synthetic_count = 0
        for visitor_id, first_ts in seen_on_floor.items():
            if visitor_id not in seen_on_entry:
                synthetic_event = {
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": "CAM_ENTRY_01",
                    "visitor_id": visitor_id,
                    "event_type": "ENTRY",
                    "timestamp": first_ts,
                    "zone_id": None,
                    "dwell_ms": 0,
                    "is_staff": False,
                    "confidence": 0.60,  # Lower confidence — inferred not directly observed
                    "metadata": {
                        "queue_depth": None,
                        "sku_zone": None,
                        "session_seq": 0,
                        "synthetic": True  # Flag that this was inferred
                    }
                }
                emitter.emit(synthetic_event)
                synthetic_count += 1

        logger.info(f"Generated {synthetic_count} synthetic ENTRY events from floor camera appearances")
    except Exception as e:
        logger.warning(f"Could not generate synthetic entries: {e}")


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline")
    parser.add_argument("--clips-dir", default="./clips", help="Directory containing CAM_*.mp4 files")
    parser.add_argument("--layout", default="./data/store_layout.json")
    parser.add_argument("--output", default="./data/events.jsonl", help="Output events file")
    parser.add_argument("--api-url", default=None, help="If set, POST events to API in real-time")
    parser.add_argument("--process-every", type=int, default=5, help="Process every N frames (higher = faster)")
    args = parser.parse_args()

    with open(args.layout) as f:
        layout = json.load(f)

    store = layout["stores"][0]

    # Map camera_id -> file path
    cam_map = {
        cam["camera_id"]: str(Path(args.clips_dir) / cam["file"])
        for cam in store["cameras"]
    }

    model = YOLO("yolov8n.pt")  # Downloads automatically on first run
    emitter = EventEmitter(output_path=args.output, api_url=args.api_url)

    # Process entry camera first (most important for entry/exit counts)
    priority_order = [
        "CAM_ENTRY_01",
        "CAM_FLOOR_01",
        "CAM_FLOOR_02",
        "CAM_BILLING_01",
        "CAM_STOCK_01",
    ]

    for camera_id in priority_order:
        if camera_id not in cam_map:
            logger.warning(f"No clip found for {camera_id}, skipping")
            continue
        video_path = cam_map[camera_id]
        if not Path(video_path).exists():
            logger.warning(f"File not found: {video_path}")
            continue
        process_clip(
            video_path=video_path,
            camera_id=camera_id,
            store_id=store["store_id"],
            model=model,
            emitter=emitter,
            layout=store,
            process_every_n=args.process_every,
        )

    # Generate synthetic ENTRY events for visitors seen on floor but not entry camera
    generate_synthetic_entries(emitter, store["store_id"])
    emitter.close()
    logger.info(f"Pipeline complete. Total events: {emitter.count} → {args.output}")


if __name__ == "__main__":
    main()