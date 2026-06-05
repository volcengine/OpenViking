OpenCode has two plugin variants with different designs. Choose the one that matches how you want to use it.

## Option 1: `opencode-memory-plugin` - Explicit tool version

Source: [examples/opencode-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-memory-plugin)

This variant exposes OpenViking memory as explicit tools through OpenCode's tool mechanism. The model decides when to call them, and data is fetched on demand.

## Option 2: `opencode/plugin` - Context injection version

Source: [examples/opencode/plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode/plugin)

This variant injects indexed code repositories into OpenCode context and starts the OpenViking server automatically when needed.
