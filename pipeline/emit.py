"""
emit.py — Writes events to JSONL file and optionally POSTs to the API in real-time.
"""

import json
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


class EventEmitter:
    def __init__(self, output_path: str, api_url: str = None, batch_size: int = 50):
        self.output_path = output_path
        self.api_url = api_url
        self.batch_size = batch_size
        self.count = 0
        self._batch = []
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._file = open(output_path, "w")

    def emit(self, event: dict):
        self._file.write(json.dumps(event) + "\n")
        self._file.flush()
        self.count += 1
        self._batch.append(event)

        if self.api_url and len(self._batch) >= self.batch_size:
            self._flush_to_api()

    def _flush_to_api(self):
        if not self._batch:
            return
        try:
            resp = requests.post(
                f"{self.api_url}/events/ingest",
                json={"events": self._batch},
                timeout=10
            )
            if resp.status_code != 200:
                logger.warning(f"API ingest returned {resp.status_code}: {resp.text[:200]}")
            else:
                result = resp.json()
                logger.info(f"Ingested batch: accepted={result.get('accepted')} duplicate={result.get('duplicate')}")
        except Exception as e:
            logger.error(f"API ingest failed: {e}")
        self._batch.clear()

    def close(self):
        if self.api_url and self._batch:
            self._flush_to_api()
        self._file.close()
        logger.info(f"Emitter closed. Total events written: {self.count}")
