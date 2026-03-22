#!/bin/bash

cd "$(dirname "$0")"

cleanup() {
    echo ""
    echo "Stopping services..."
    kill $FRONTEND_PID 2>/dev/null
    wait $FRONTEND_PID 2>/dev/null
    docker compose -f backend/docker-compose.yml down
    echo "Done."
    exit 0
}

trap cleanup SIGINT SIGTERM

echo "Starting backend (Docker) on http://localhost:8000 ..."
docker compose -f backend/docker-compose.yml up --build -d

# Wait for backend to be ready
echo "Waiting for backend..."
until curl -sf http://localhost:8000/docs > /dev/null 2>&1; do sleep 1; done
echo "Backend is ready."


echo "Starting frontend on http://localhost:3000 ..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://localhost:8000  (docs: http://localhost:8000/docs)"
echo "Frontend: http://localhost:3000"
echo "Press Ctrl+C to stop all services."
echo ""

wait $FRONTEND_PID
