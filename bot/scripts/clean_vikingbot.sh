#!/bin/bash
BOT_DIR="$HOME/.openviking/data/bot"

echo "🧹 Cleaning VikingBot data directory..."
echo "📂 Cleaning contents of: $BOT_DIR"

if [ -d "$BOT_DIR" ]; then
    echo "🗑️  Deleting items:"
    for item in "$BOT_DIR"/*; do
        if [ -e "$item" ]; then
            echo "   - $(basename "$item")"
            rm -rf "$item"
        fi
    done
    echo "✅ Done!"
else
    echo "⚠️  Directory does not exist: $BOT_DIR"
fi

