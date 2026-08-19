#!/bin/bash

# Run servo tuning page server and WebSocket server
# HTTP server on port 8081, WebSocket server on port 8766

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# gRPC honors http_proxy/https_proxy even for 127.0.0.1 targets (its no_proxy
# parser doesn't understand glob patterns like "127.*"), so a system proxy
# can silently break local servo <-> gRPC connections. Never proxy this.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

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

# Use face conda env python if available, otherwise fall back to system python3
PYTHON="${CONDA_PREFIX}/bin/python3"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

# Start HTTP server
"$PYTHON" -m http.server $HTTP_PORT &
HTTP_PID=$!

# Start WebSocket server
"$PYTHON" servo_server.py --port $WS_PORT &
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
