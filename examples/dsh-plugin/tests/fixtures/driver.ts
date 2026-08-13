#!/usr/bin/env node
/**
 * Test driver: starts the OpenViking stub, isolates all shared state
 * (credentials, pending queue, recall state) into the temp cwd, boots one
 * real Loader composition, runs two turns, waits for the plugin's async
 * mirroring to reach the stub, and writes the recorded stub traffic for the
 * e2e test to assert on.
 */

import { writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { boot, resolveConfigPath } from '@deepseek-ai/dsh-app-boot'
import { runFixtureTurn } from '@deepseek-ai/dsh-loader-smoke'
import { startOvStub } from './ov-stub.ts'

const configPath = process.argv[2]
if (configPath === undefined) throw new Error('dsh-plugin driver requires a config path')

const stub = await startOvStub()
process.env.OV_STUB_URL = stub.baseUrl
// Isolate every shared-runtime side channel into the temp cwd: no machine
// credentials, no shared pending queue, no cross-run recall state.
process.env.OPENVIKING_CLI_CONFIG_FILE = join(process.cwd(), 'no-ovcli.conf')
process.env.OPENVIKING_CONFIG_FILE = join(process.cwd(), 'no-ov.conf')
process.env.OPENVIKING_PENDING_DIR = join(process.cwd(), '.ov-pending')
process.env.OPENVIKING_STATE_DIR = join(process.cwd(), '.ov-state')
delete process.env.OPENVIKING_CREDENTIAL_SOURCE
delete process.env.OPENVIKING_CREDENTIALS_SOURCE
delete process.env.OPENVIKING_BASE_URL
delete process.env.OPENVIKING_URL
delete process.env.OPENVIKING_API_KEY

const ctx = await boot('openviking-dsh-plugin-e2e', resolveConfigPath(configPath, undefined))
try {
  await runFixtureTurn(ctx, { task: 'first task about the dsh e2e note' })
  await runFixtureTurn(ctx, { task: 'second task about the dsh e2e note' })
  // Mirroring and commits are fire-and-forget; wait for both commits.
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const commits = stub.requests.filter(item => item.path.endsWith('/commit')).length
    if (commits >= 2) break
    await sleep(100)
  }
} finally {
  await ctx.fiber.dispose()
  await writeFile('./ov-stub-log.json', JSON.stringify(stub.requests, null, 2))
  await stub.close()
}
