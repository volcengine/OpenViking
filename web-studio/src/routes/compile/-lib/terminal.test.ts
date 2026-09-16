import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { OvClientError } from '#/lib/ov-client'
import { runCompileCommand, runCompileSubmission } from './terminal'
import { CompileCommandError } from './commands'
import en from '#/i18n/locales/en/compile'
import zh from '#/i18n/locales/zh-CN/compile'

const api = vi.hoisted(() => ({
  createCompile: vi.fn(),
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

it('releases a rejected submission so its parameters can be corrected', async () => {
  const pending = { current: null }
  api.createCompile.mockRejectedValueOnce(
    new OvClientError({ code: 'INVALID_ARGUMENT', message: 'Invalid URI' }),
  )
  const command =
    'compile --from viking://resources/a --to viking://resources/b --skill viking://agent/skills/s'
  await expect(
    runCompileSubmission(command, pending, String, vi.fn()),
  ).rejects.toThrow('Invalid URI')
  expect(pending.current).toBeNull()
})
