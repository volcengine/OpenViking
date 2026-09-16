import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { runCompileCommand } from './terminal'
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
