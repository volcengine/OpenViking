#!/bin/bash
set -e

echo "Waiting for Ollama service to be ready..."
until ollama list > /dev/null 2>&1; do
  echo "Ollama service not ready yet, waiting..."
  sleep 2
done

echo "Ollama service is ready!"
echo "Pulling nomic-embed-text model..."
ollama pull nomic-embed-text

echo "Model pull complete!"
ollama list
