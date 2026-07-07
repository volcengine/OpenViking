import test from "node:test"
import assert from "node:assert/strict"
import { applyRepoSystemPrompt } from "../lib/repo-context.mjs"

const REPO_PROMPT = "## OpenViking - Indexed Code Repositories\n- **demo** (viking://resources/demo/)"

test("merges repo context into the existing system entry without adding a block", () => {
  const output = { system: ["BASE SYSTEM PROMPT"] }
  const injected = applyRepoSystemPrompt(output, REPO_PROMPT)

  assert.equal(injected, true)
  // Single system block must be preserved so OpenCode emits one system message.
  assert.equal(output.system.length, 1)
  assert.ok(output.system[0].includes("BASE SYSTEM PROMPT"))
  assert.ok(output.system[0].includes("Indexed Code Repositories"))
})

test("pushes when the system array is empty", () => {
  const output = { system: [] }
  applyRepoSystemPrompt(output, REPO_PROMPT)
  assert.deepEqual(output.system, [REPO_PROMPT])
})

test("does not prepend blank lines when the existing entry is empty", () => {
  const output = { system: [""] }
  applyRepoSystemPrompt(output, REPO_PROMPT)
  assert.deepEqual(output.system, [REPO_PROMPT])
})

test("is a no-op when there is no repo prompt", () => {
  const output = { system: ["BASE"] }
  assert.equal(applyRepoSystemPrompt(output, null), false)
  assert.equal(applyRepoSystemPrompt(output, ""), false)
  assert.deepEqual(output.system, ["BASE"])
})

test("is a no-op when output has no system array", () => {
  assert.equal(applyRepoSystemPrompt({}, REPO_PROMPT), false)
  assert.equal(applyRepoSystemPrompt(undefined, REPO_PROMPT), false)
})

// Regression guard for issue #2885: replicate OpenCode's system serialization
// (session/llm/request.ts) and assert the injected repo context does not create
// a second leading system message, which litellm -> OpenAI rejects with
// "System message must be at the beginning".
test("regression #2885: yields a single leading system message after OpenCode serialization", () => {
  // OpenCode collapses agent/provider/user system prompts into one entry.
  const system = ["OpenCode base system prompt"]
  const header = system[0]

  // The plugin's experimental.chat.system.transform hook runs here.
  applyRepoSystemPrompt({ system }, REPO_PROMPT)

  // OpenCode only re-collapses when length > 2.
  if (system.length > 2 && system[0] === header) {
    const rest = system.slice(1)
    system.length = 0
    system.push(header, rest.join("\n"))
  }

  const messages = [
    ...system.map((content) => ({ role: "system", content })),
    { role: "user", content: "what does the demo repo do?" },
  ]

  const systemMessages = messages.filter((m) => m.role === "system")
  assert.equal(systemMessages.length, 1)
  assert.equal(messages[0].role, "system")
  assert.notEqual(messages[1].role, "system")
  assert.ok(messages[0].content.includes("Indexed Code Repositories"))
})
