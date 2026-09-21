import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { OvClientError } from '#/lib/ov-client'
import { runCompileCommand, runCompileSubmission } from './terminal'
import { CompileCommandError } from './commands'
import en from '#/i18n/locales/en/compile'
import zh from '#/i18n/locales/zh-CN/compile'

const api = vi.hoisted(() => ({
  createCompile: vi.fn(),
  lookupSubmission: vi.fn(),
  fetchCompileTasks: vi.fn(),
  fetchCompileTask: vi.fn(),
  cancelCompile: vi.fn(),
}))
vi.mock('./api', () => api)
beforeEach(() => vi.resetAllMocks())

describe('Compile terminal regressions', () => {
  it('keeps status filters in the executable next-page command', async () => {
    api.fetchCompileTasks
      .mockResolvedValueOnce({ items: [], next_cursor: 'signed-cursor' })
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    const page = await runCompileCommand('task list --status running', 'key')
    await runCompileCommand(page.body.trim(), 'key')
    expect(api.fetchCompileTasks.mock.calls).toEqual([
      ['running', '', undefined],
      ['running', '', 'signed-cursor'],
    ])
  })

  it.each(['en', 'zh-CN'])(
    'localizes known statuses and errors in %s without echoing private args',
    async (lng) => {
      const i18n = createInstance()
      await i18n.init({
        lng,
        resources: { en: { compile: en }, 'zh-CN': { compile: zh } },
        defaultNS: 'compile',
      })
      api.fetchCompileTask.mockResolvedValue({
        task_id: 'task-1',
        status: 'running',
        stage: 'custom-stage',
      })
      const result = await runCompileCommand(
        'task status task-1',
        'key',
        (status) => i18n.t(`statuses.${status}`, { defaultValue: status }),
      )
      expect(result.body).toContain(lng === 'en' ? 'Running' : '运行中')
      expect(result.body).toContain('custom-stage')
      const input = `compile --from viking://resources/a --to viking://resources/b --skill viking://agent/skills/s --args='{"api_key":"private-example"}'`
      const error: unknown = await runCompileCommand(input, 'key').catch(
        (cause: unknown) => cause,
      )
      expect(error).toBeInstanceOf(CompileCommandError)
      if (!(error instanceof CompileCommandError))
        throw new Error('Expected command error')
      const storedEntry = JSON.stringify({
        body: i18n.t(`commandErrors.${error.code}`),
      })
      expect(error.message).not.toContain('private-example')
      expect(storedEntry).not.toContain('private-example')
      expect(storedEntry).not.toContain('commandErrors.')
      expect(api.createCompile).not.toHaveBeenCalled()
    },
  )
})

it('retains the creation key across response loss and task queries', async () => {
  const pending = { current: null }
  const saveKey = vi.fn()
  const status = (value: string) => value
  const command =
    'compile --from viking://resources/a --to viking://resources/b --skill viking://agent/skills/s'
  const tasks = new Map<string, string>()
  api.createCompile.mockImplementation(async (_body, key: string) => {
    if (!tasks.has(key)) tasks.set(key, `task-${tasks.size + 1}`)
    if (api.createCompile.mock.calls.length === 1)
      throw new Error('response lost')
    return { task_id: tasks.get(key), status: 'pending' }
  })
  api.fetchCompileTasks.mockResolvedValue({ items: [], next_cursor: null })
  await expect(
    runCompileSubmission(command, pending, status, saveKey),
  ).rejects.toThrow('response lost')
  await runCompileSubmission('task list', pending, status, saveKey)
  await expect(
    runCompileSubmission(
      command + ' --instruction changed',
      pending,
      status,
      saveKey,
    ),
  ).rejects.toThrow('pendingSubmission')
  const recovered = await runCompileSubmission(
    command,
    pending,
    status,
    saveKey,
  )
  expect(recovered.taskId).toBe('task-1')
  expect(tasks.size).toBe(1)
  expect(api.createCompile.mock.calls[0][1]).toBe(
    api.createCompile.mock.calls[1][1],
  )
  expect(pending.current).toBeNull()
  expect(saveKey).toHaveBeenLastCalledWith(null)
})

it.each(['INVALID_ARGUMENT', 'INVALID_URI'])(
  'releases %s so parameters can be corrected',
  async (code) => {
    const pending = { current: null }
    api.createCompile.mockRejectedValueOnce(
      new OvClientError({ code, message: 'Invalid URI' }),
    )
    const command =
      'compile --from viking://resources/a --to viking://resources/b --skill viking://agent/skills/s'
    await expect(
      runCompileSubmission(command, pending, String, vi.fn()),
    ).rejects.toThrow('Invalid URI')
    expect(pending.current).toBeNull()
  },
)

const retryCommand =
  'compile --from viking://resources/a --to viking://resources/b --skill viking://agent/skills/s'
it('recovers a persisted submission after remount without creating again', async () => {
  let saved: string | null = null
  const save = (key: string | null) => {
    saved = key
  }
  api.createCompile.mockRejectedValueOnce(new Error('response lost'))
  await expect(
    runCompileSubmission(retryCommand, { current: null }, String, save),
  ).rejects.toThrow('response lost')
  const originalKey = saved
  api.lookupSubmission.mockResolvedValueOnce({
    task_id: 'original-task',
    status: 'pending',
  })
  const result = await runCompileSubmission(
    retryCommand,
    { current: null },
    String,
    save,
    () => saved,
  )
  expect(result.taskId).toBe('original-task')
  expect(api.lookupSubmission).toHaveBeenCalledWith(originalKey)
  expect(api.createCompile).toHaveBeenCalledTimes(1)
  expect(saved).toBeNull()
})
it('reuses the persisted key when lookup races creation, including after conflict', async () => {
  const key = `${Date.now()}:saved-key`
  const pending = { current: null }
  const save = vi.fn()
  api.lookupSubmission.mockRejectedValue(
    new OvClientError({ code: 'NOT_FOUND', message: 'not found' }),
  )
  api.createCompile.mockRejectedValueOnce(
    new OvClientError({ code: 'CONFLICT', message: 'different request' }),
  )
  await expect(
    runCompileSubmission(
      retryCommand + ' --instruction changed',
      pending,
      String,
      save,
      () => key,
    ),
  ).rejects.toThrow('different request')
  expect(pending.current).toBeNull()
  expect(save).not.toHaveBeenCalledWith(null)
  api.createCompile.mockResolvedValueOnce({
    task_id: 'original-task',
    status: 'pending',
  })
  await runCompileSubmission(retryCommand, pending, String, save, () => key)
  expect(api.createCompile.mock.calls.map((call) => call[1])).toEqual([
    key,
    key,
  ])
})
it('does not create after a failed recovery lookup or expired key', async () => {
  api.lookupSubmission.mockRejectedValueOnce(new Error('offline'))
  await expect(
    runCompileSubmission(
      retryCommand,
      { current: null },
      String,
      vi.fn(),
      () => `${Date.now()}:saved`,
    ),
  ).rejects.toThrow('offline')
  api.lookupSubmission.mockRejectedValueOnce(
    new OvClientError({ code: 'NOT_FOUND', message: 'not found' }),
  )
  await expect(
    runCompileSubmission(
      retryCommand,
      { current: null },
      String,
      vi.fn(),
      () => `1:expired`,
    ),
  ).rejects.toThrow('expiredSubmission')
  expect(api.createCompile).not.toHaveBeenCalled()
})

it('localizes known stages while preserving unknown provider stages', async () => {
  const i18n = createInstance()
  await i18n.init({
    lng: 'zh-CN',
    resources: { 'zh-CN': { compile: zh } },
    defaultNS: 'compile',
  })
  const localizeStage = (stage: string) =>
    i18n.t(`stages.${stage.replace(/^compile:\s*/, '')}`, {
      defaultValue: stage,
    })
  for (const stage of ['compile: queued', 'custom-stage']) {
    api.fetchCompileTask.mockResolvedValueOnce({
      task_id: 't',
      status: 'running',
      stage,
    })
    const result = await runCompileSubmission(
      'task status t',
      { current: null },
      String,
      vi.fn(),
      () => null,
      localizeStage,
    )
    expect(result.body).toContain(
      stage === 'custom-stage' ? stage : i18n.t('stages.queued'),
    )
    if (stage !== 'custom-stage')
      expect(result.body).not.toContain('compile: queued')
  }
})
