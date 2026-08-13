import { readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
import { LOADER_SMOKE_TEST_TIMEOUT_MS, runLoaderSmoke } from '@deepseek-ai/dsh-loader-smoke'

const driver = fileURLToPath(new URL('./fixtures/driver.ts', import.meta.url))
const configPath = fileURLToPath(new URL('./fixtures/dsh-plugin.cordis.yml', import.meta.url))
const tsconfigPath = fileURLToPath(new URL('../tsconfig.json', import.meta.url))

interface StubRequest {
  method: string
  path: string
  body: unknown
}

async function jsonlFiles(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true })
  const paths = await Promise.all(entries.map(async (entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return jsonlFiles(path)
    return entry.isFile() && entry.name.endsWith('.jsonl') ? [path] : []
  }))
  return paths.flat()
}

describe('openviking dsh plugin through a real headless cordis.yml', () => {
  it('captures the conversation into OpenViking and injects shared-runtime recall blocks', async () => {
    let events: SessionEvent[] = []
    let stubLog: StubRequest[] = []
    const { stderr } = await runLoaderSmoke({
      label: 'openviking-dsh-plugin headless smoke',
      tempDirPrefix: 'openviking-dsh-plugin-e2e-',
      binScript: driver,
      libBinScript: driver,
      configPath,
      tsconfigPath,
      mode: 'src',
      inspect: async (cwd) => {
        const logs = await jsonlFiles(join(cwd, '.sessions'))
        expect(logs).toHaveLength(1)
        const lines = (await readFile(logs[0] as string, 'utf8')).trimEnd().split('\n')
        events = lines.slice(1).map(line => JSON.parse(line) as SessionEvent)
        stubLog = JSON.parse(await readFile(join(cwd, 'ov-stub-log.json'), 'utf8')) as StubRequest[]
      },
    })
    expect(stderr).not.toContain('UNHANDLED')
    expect(events.filter(event => event.type === 'turn/end')).toHaveLength(2)

    // ---- recall: the shared block injected as a durable, attributed user message ----
    const recalls = events.filter(
      (event): event is SessionEvent<'user/message'> =>
        event.type === 'user/message' && event.data.source.kind === 'plugin',
    )
    // The raw-find fallback has no cross-turn ledger: one injection per turn.
    expect(recalls).toHaveLength(2)
    const firstStepStart = events.find(event => event.type === 'step/start')
    expect(firstStepStart).toBeDefined()
    for (const recall of recalls) {
      expect(recall.surfaceOp).toBe('append')
      expect(recall.data.source).toMatchObject({
        kind: 'plugin',
        plugin: 'openviking',
        form: 'recall',
      })
      const text = recall.data.content
        .filter(block => block.type === 'text')
        .map(block => block.text)
        .join('\n')
      expect(text).toContain('<openviking-context>')
      expect(text).toContain('concise answers')
      expect(recall.seq).toBeGreaterThan(firstStepStart!.seq)
    }

    // The injected context reaches the request surface but never the headers.
    const headers = events.filter(event => event.type === 'request/header')
    expect(JSON.stringify(headers)).not.toContain('openviking-context')

    // ---- recall traffic: context face 404s, then the find fallback ----
    expect(stubLog.filter(request => request.path === '/api/v1/search/search').length).toBeGreaterThanOrEqual(1)
    expect(stubLog.filter(request => request.path === '/api/v1/search/find').length).toBeGreaterThanOrEqual(2)

    // ---- capture: the conversation was mirrored and committed ----
    const messagePosts = stubLog.filter(request =>
      request.method === 'POST' && /\/api\/v1\/sessions\/[^/]+\/messages/.test(request.path))
    expect(messagePosts.length).toBeGreaterThanOrEqual(1)
    const mirroredText = JSON.stringify(messagePosts.map(request => request.body))
    expect(mirroredText).toContain('first task about the dsh e2e note')
    expect(mirroredText).toContain('second task about the dsh e2e note')
    expect(mirroredText).toContain('ov plugin mock reply')
    // The plugin's own injected recall must not echo back into memory.
    expect(mirroredText).not.toContain('openviking-context')
    const commits = stubLog.filter(request => request.path.endsWith('/commit'))
    expect(commits.length).toBeGreaterThanOrEqual(2)
    // One mirrored session, derived via the family session-id scheme.
    const sessionIds = new Set(messagePosts.map(request => request.path.split('/')[4]))
    expect(sessionIds.size).toBe(1)
    expect([...sessionIds][0]).toMatch(/^dsh-/)
  }, LOADER_SMOKE_TEST_TIMEOUT_MS)
})
