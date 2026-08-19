#!/bin/bash

# Run servo tuning page server and WebSocket server
# HTTP server on port 8081, WebSocket server on port 8766

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

HTTP_PORT=8081
WS_PORT=8766

echo "=========================================="
echo "  Servo Tuning Server"
echo "=========================================="
echo "HTTP Server: http://localhost:$HTTP_PORT/servo_tuning.html"
echo "WebSocket:   ws://localhost:$WS_PORT"
echo "=========================================="
echo "Press Ctrl+C to stop all servers"
echo ""

# Start HTTP server
python3 -m http.server $HTTP_PORT &
HTTP_PID=$!

# Start WebSocket server
python3 servo_server.py --port $WS_PORT &
WS_PID=$!

# Cleanup handler
cleanup() {
    echo ""
    echo "Stopping servers..."
    kill $HTTP_PID $WS_PID 2>/dev/null
    wait 2>/dev/null
    echo "Servers stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait for both
wait
