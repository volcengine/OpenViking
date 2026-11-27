# OpenCode 1.x

`@openviking/opencode-plugin` 1.0 and later supports OpenCode 2 only (the `setup()` plugin API). Loaded by OpenCode 1.x through `server()`, it logs one line saying so and does nothing.

- Last line that supports OpenCode 1.x (1.15.7 and later): the 0.x releases of `@openviking/opencode-plugin` on npm (`@openviking/opencode-plugin@0`).
- Its source is in this repository's history, under `examples/opencode-plugin` before the commit that added this file: `git log --diff-filter=A --format=%H -- examples/unmaintained/opencode-1.x.md` names that commit, and its parent has the code.
- The 0.x line gets no fixes.
