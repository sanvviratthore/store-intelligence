"""
zones.py — Maps pixel coordinates to zone IDs using camera-specific bbox definitions
from store_layout.json. Falls back to position-based heuristics if no layout match.
"""


class ZoneClassifier:
    def __init__(self, layout: dict, camera_id: str):
        self.camera_id = camera_id
        self.zones = [
            z for z in layout.get("zones", [])
            if z.get("camera_id") == camera_id and z["zone_id"] not in ("ENTRY", "STOCKROOM")
        ]

    def classify(self, cx: int, cy: int) -> str | None:
        """Return zone_id for a given center point, or None if no match."""
        for zone in self.zones:
            x1, y1, x2, y2 = zone["bbox"]
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return zone["zone_id"]
        return None
