#!/usr/bin/env bash
# uvic-course-history one-liner.
#   bash run.sh                  -> build cache if needed, refresh, open report.html
#   bash run.sh CSC 370          -> terminal card for that course
#   bash run.sh CSC 421 432 436  -> a batch
set -e
cd "$(dirname "$0")"

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || { echo "python 3 required"; exit 1; }

if [ ! -f data/offerings.json ]; then
    echo "first run: syncing ~10 years of UVic banner data (few minutes, one time)"
    "$PY" uvic_courses.py sync
else
    "$PY" uvic_courses.py sync --years 3
fi

if [ $# -eq 0 ]; then
    "$PY" uvic_courses.py report
    open report.html 2>/dev/null || xdg-open report.html 2>/dev/null || echo "report saved to report.html"
else
    "$PY" uvic_courses.py check "$@"
fi
