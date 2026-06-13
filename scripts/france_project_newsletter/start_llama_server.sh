#!/usr/bin/env bash
# Start llama-server for the GPS Newsletter pipeline.
# Picks the best available model from MODELS_DIR in priority order.
# Runs on port 8181 with full GPU offload (RTX 4060 8GB).
set -euo pipefail

LLAMA_SERVER="${LLAMA_SERVER:-/home/alx/bin/llama-server}"
MODELS_DIR="${MODELS_DIR:-/home/alx/Documents/models/llm}"
HOST="${LLAMA_HOST:-127.0.0.1}"
PORT="${LLAMA_PORT:-8181}"

# Priority order: best model for French news classification + structured JSON output.
# Add qwen2.5-7b-instruct-q4_k_m.gguf to MODELS_DIR when available — it will
# automatically become the preferred model.
PREFERRED_MODELS=(
    "qwen2.5-7b-instruct-q4_k_m.gguf"        # ideal — add when available
    "Qwen3.5-9B.Q4_K_M.gguf"                  # strong multilingual, good JSON
    "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"  # solid fallback
)

MODEL_PATH=""
for name in "${PREFERRED_MODELS[@]}"; do
    candidate="$MODELS_DIR/$name"
    if [[ -f "$candidate" ]]; then
        MODEL_PATH="$candidate"
        break
    fi
done

if [[ -z "$MODEL_PATH" ]]; then
    echo "ERROR: No suitable model found in $MODELS_DIR"
    echo "Expected one of:"
    for name in "${PREFERRED_MODELS[@]}"; do
        echo "  $MODELS_DIR/$name"
    done
    exit 1
fi

MODEL_NAME="$(basename "$MODEL_PATH")"
echo "Starting llama-server with model: $MODEL_NAME"
echo "  Host: $HOST:$PORT"

exec "$LLAMA_SERVER" \
    --model "$MODEL_PATH" \
    --host "$HOST" \
    --port "$PORT" \
    --n-gpu-layers 99 \
    --ctx-size 6144 \
    --parallel 2 \
    --flash-attn auto \
    --log-disable
