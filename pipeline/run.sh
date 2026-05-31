#!/bin/bash
# run.sh — One command to process all clips and feed events into the API
# Usage: bash pipeline/run.sh [clips_dir] [api_url]

CLIPS_DIR=${1:-./clips}
API_URL=${2:-http://localhost:8000}
OUTPUT=./data/events.jsonl

echo "================================================"
echo " Purplle Store Intelligence — Detection Pipeline"
echo "================================================"
echo "Clips dir : $CLIPS_DIR"
echo "API URL   : $API_URL"
echo "Output    : $OUTPUT"
echo ""

# Check clips exist
if [ ! -d "$CLIPS_DIR" ]; then
  echo "ERROR: clips directory not found: $CLIPS_DIR"
  echo "Create the directory and add CAM_1.mp4 through CAM_5.mp4"
  exit 1
fi

# Run detection pipeline
python -m pipeline.detect \
  --clips-dir "$CLIPS_DIR" \
  --output "$OUTPUT" \
  --api-url "$API_URL" \
  --process-every 5

echo ""
echo "Pipeline complete. Events written to $OUTPUT"
echo "View live dashboard at: $API_URL"
