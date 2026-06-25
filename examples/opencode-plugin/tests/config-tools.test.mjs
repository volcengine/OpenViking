import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { loadConfig } from "../lib/utils.mjs"
import { buildToolRegistry } from "../index.mjs"

test("memory tools can be disabled without disabling code tools", async () => {
  const project = await mkdtemp(join(tmpdir(), "openviking-plugin-test-"))
  try {
    await mkdir(join(project, ".opencode"))
    await writeFile(
      join(project, ".opencode", "openviking-config.json"),
      JSON.stringify({ memoryTools: { enabled: false } }),
    )

    const config = loadConfig(project, project)
    const tools = buildToolRegistry({ config, sessionManager: {}, projectDirectory: project })

    assert.equal(config.memoryTools.enabled, false)
    assert.ok(tools.codesearch)
    assert.equal(tools.memsearch, undefined)
  } finally {
    await rm(project, { recursive: true, force: true })
  }
})

test("unused code navigation tools can be omitted from the registry", async () => {
  const project = await mkdtemp(join(tmpdir(), "openviking-plugin-test-"))
  try {
    await mkdir(join(project, ".opencode"))
    await writeFile(
      join(project, ".opencode", "openviking-config.json"),
      JSON.stringify({
        memoryTools: { enabled: false },
        codeTools: { outline: false, expand: false },
      }),
    )

    const config = loadConfig(project, project)
    const tools = buildToolRegistry({ config, sessionManager: {}, projectDirectory: project })

    assert.ok(tools.codesearch)
    assert.equal(tools.codeoutline, undefined)
    assert.equal(tools.codeexpand, undefined)
  } finally {
    await rm(project, { recursive: true, force: true })
  }
})
