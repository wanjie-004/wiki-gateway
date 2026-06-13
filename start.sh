#!/bin/bash
# 启动 Wiki Gateway
set -e

cd "$(dirname "$0")"

# 用 hermes-agent venv（已装 fastapi/uvicorn 等）
PYTHON=/mnt/d/wsl/.hermes/hermes-agent/venv/bin/python
PORT=${GATEWAY_PORT:-8765}

echo "🚀 启动 Wiki Gateway (端口 $PORT)..."
exec $PYTHON -m uvicorn app.main:app \
    --host 127.0.0.1 \
    --port $PORT \
    --log-level info
