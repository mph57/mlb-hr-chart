#!/bin/bash
# Start the HR board locally, then open it in your browser.
cd "$(dirname "$0")" || exit 1

# Start the server in the background.
.venv/bin/python wsgi.py &
SERVER_PID=$!

# Give it a moment, then open the page.
sleep 3
open "http://127.0.0.1:5001/today"

echo "HR board running at http://127.0.0.1:5001/today  (Ctrl+C to stop)"
wait $SERVER_PID
