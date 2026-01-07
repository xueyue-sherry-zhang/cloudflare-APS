#!/bin/bash
# Quick script to check scraper progress

cd "$(dirname "$0")"

echo "=== Scraper Status ==="
if [ -f scraper.pid ]; then
    PID=$(cat scraper.pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "✓ Script is RUNNING (PID: $PID)"
    else
        echo "✗ Script is NOT running"
    fi
else
    echo "? PID file not found"
fi

echo ""
echo "=== Recent Log Output ==="
tail -20 scraper_output.log 2>/dev/null || echo "No log output yet"

echo ""
echo "=== Results Files ==="
if [ -f aps_summit_all_events_temp.csv ]; then
    LINES=$(wc -l < aps_summit_all_events_temp.csv)
    echo "✓ Temporary results: $LINES events found"
    echo "  File: aps_summit_all_events_temp.csv"
else
    echo "⏳ No temporary results yet (saves every 100 events)"
fi

if [ -f aps_summit_superconducting_qubits_temp.csv ]; then
    LINES=$(wc -l < aps_summit_superconducting_qubits_temp.csv)
    echo "✓ Superconducting qubit results: $LINES events"
    echo "  File: aps_summit_superconducting_qubits_temp.csv"
else
    echo "⏳ No superconducting qubit results yet"
fi

echo ""
echo "=== To monitor live ==="
echo "  tail -f scraper_output.log"

