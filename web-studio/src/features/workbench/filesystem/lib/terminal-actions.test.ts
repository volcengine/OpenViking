import {
  parseAddMemory,
  parseAddResource,
  parseAddSkill,
  parseMkdir,
  parseRemove,
  parseTransfer,
  submitMemoryCommand,
  taskResultDetails,
  taskResultResourceUri,
  waitForResourceTask,
} from '#/features/workbench/filesystem/lib/terminal-actions'
import { describe, expect, it, vi } from 'vitest'

describe('terminal import commands', () => {
  it('stops a memory submission when its connection changes during session creation', async () => {
    let finishCreate!: (session: { session_id: string }) => void
    const createSession = vi.fn(
      () =>
        new Promise<{ session_id: string }>((resolve) => {
          finishCreate = resolve
        }),
    )
    const addMessage = vi.fn().mockResolvedValue(undefined)
    const commitSession = vi.fn().mockResolvedValue({ task_id: 'task' })
    let active = true
    const submission = submitMemoryCommand('secret', undefined, () => active, {
      createSession,
      addMessage,
      commitSession,
    })
    active = false
    finishCreate({ session_id: 'old-session' })
    expect(await submission).toBeUndefined()
    expect(addMessage).not.toHaveBeenCalled()
    expect(commitSession).not.toHaveBeenCalled()
  })

  it('does not commit a memory after switching connections during addMessage', async () => {
    let finishMessage!: () => void
    const addMessage = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishMessage = resolve
        }),
    )
    const commitSession = vi.fn().mockResolvedValue({ task_id: 'task' })
    let active = true
    const submission = submitMemoryCommand(
      'secret',
      'old-session',
      () => active,
      {
        createSession: vi.fn(),
        addMessage,
        commitSession,
      },
    )
    active = false
    finishMessage()
    expect(await submission).toBeUndefined()
    expect(commitSession).not.toHaveBeenCalled()
  })
  it('accepts quoted memory content and a session ID', () => {
    expect(
      parseAddMemory('/add-memory "a useful fact" --session-id session-1'),
    ).toEqual({
      content: 'a useful fact',
      sessionId: 'session-1',
    })
  })

  it('can create a new session for a one-shot memory command', () => {
    expect(parseAddMemory('/add-memory "a useful fact"')).toEqual({
      content: 'a useful fact',
      sessionId: undefined,
    })
  })

  it('accepts a Git URL and rejects host filesystem paths', () => {
    expect(
      parseAddSkill('/add-skill https://example.com/skill.git --scope agent'),
    ).toEqual({
      source: 'https://example.com/skill.git',
      targetUri: 'viking://agent/skills',
    })
    expect(
      parseAddSkill(
        '/add-skill https://example.com/skill.git -p viking://~/skills',
      ),
    ).toEqual({
      source: 'https://example.com/skill.git',
      targetUri: 'viking://~/skills',
    })
    expect(() => parseAddSkill('/add-skill ./local/skill')).toThrow()
  })

  it('maps browser-safe resource URLs to the open-source resource API', () => {
    expect(
      parseAddResource(
        `ov add-resource https://github.com/volcengine/OpenViking --to viking://resources/openviking-repo --args '{"branch":"main"}'`,
      ),
    ).toEqual({
      path: 'https://github.com/volcengine/OpenViking',
      to: 'viking://resources/openviking-repo',
      args: { branch: 'main' },
    })
    expect(() =>
      parseAddResource('/add-resource https://example.com --args []'),
    ).toThrow()
    expect(() => parseAddResource('/add-resource ./local.md')).toThrow()
    expect(() =>
      parseAddResource(
        '/add-resource https://example.com --to viking://a --parent viking://b',
      ),
    ).toThrow()
  })
})

describe('resource task completion', () => {
  it('opens the task-produced resource before a requested fallback path', () => {
    expect(
      taskResultResourceUri(
        {
          status: 'completed',
          resource_id: 'viking://resources/generated',
        },
        'viking://resources/requested',
      ),
    ).toBe('viking://resources/generated')
    expect(
      taskResultResourceUri(
        {
          status: 'completed',
          resource_id: 'opaque-task-id',
          result: { root_uri: 'viking://agent/skills/demo' },
        },
        'viking://resources/requested',
      ),
    ).toBe('viking://agent/skills/demo')
  })
  it('shows only fields returned by a completed task, including zero auxiliary files', () => {
    expect(
      taskResultDetails('task-1', {
        status: 'completed',
        result: {
          root_uri: 'viking://agent/skills/demo',
          name: 'demo',
          auxiliary_files: 0,
        },
      }),
    ).toEqual([
      { key: 'taskId', value: 'task-1' },
      {
        key: 'rootUri',
        value: 'viking://agent/skills/demo',
        uri: 'viking://agent/skills/demo',
      },
      { key: 'name', value: 'demo' },
      { key: 'auxiliaryFiles', value: '0' },
    ])
  })
  it('does not treat task submission or running as a completed import', async () => {
    const fetchTask = vi
      .fn()
      .mockResolvedValueOnce({ status: 'pending' })
      .mockResolvedValueOnce({ status: 'running' })
      .mockResolvedValueOnce({
        status: 'failed',
        error: 'Source URL returned HTTP 404',
      })
    const task = await waitForResourceTask('task-1', fetchTask, {
      wait: async () => {},
    })
    expect(fetchTask).toHaveBeenCalledTimes(3)
    expect(task).toEqual({
      status: 'failed',
      error: 'Source URL returned HTTP 404',
    })
  })
})

describe('terminal resource deletion', () => {
  it.each(['/rm', 'rm', 'ov rm'])(
    'accepts %s with an explicit target',
    (prefix) => {
      expect(
        parseRemove(`${prefix} "viking://resources/old notes.md"`),
      ).toEqual({
        uri: 'viking://resources/old notes.md',
        recursive: false,
      })
    },
  )
  it.each(['-r', '--recursive'])(
    'accepts recursive flag %s on either side of the target',
    (flag) => {
      for (const args of [
        `${flag} viking://resources/old`,
        `viking://resources/old ${flag}`,
      ]) {
        expect(parseRemove(`/rm ${args}`)).toEqual({
          uri: 'viking://resources/old',
          recursive: true,
        })
      }
    },
  )
  it.each([
    '/rm',
    '/rm --recursive',
    '/rm viking://',
    '/rm /tmp/file',
    '/rm viking://resources/a viking://resources/b',
    '/rm viking://resources/a --force',
    '/rm viking://resources/a -r --recursive',
  ])('rejects ambiguous or unsupported deletion: %s', (command) => {
    expect(() => parseRemove(command)).toThrow('rmUsage')
  })
})

describe('terminal directory creation and transfers', () => {
  it('preserves quoted paths and directory descriptions', () => {
    expect(
      parseMkdir(
        'ov mkdir "viking://resources/new docs" --description "Project notes"',
      ),
    ).toEqual({
      uri: 'viking://resources/new docs',
      description: 'Project notes',
    })
  })
  it.each([
    '/mkdir',
    '/mkdir viking://',
    '/mkdir viking:////',
    '/mkdir /tmp/docs',
    '/mkdir viking://resources/docs extra',
    '/mkdir viking://resources/docs --description',
    '/mkdir viking://resources/docs --force',
  ])('rejects invalid directory creation: %s', (command) => {
    expect(() => parseMkdir(command)).toThrow('mkdirUsage')
  })
  it('moves a quoted file URI without adding copy-only options', () => {
    expect(
      parseTransfer(
        '/mv "viking://resources/a b.md" "viking://resources/c d.md"',
        'mv',
      ),
    ).toEqual({
      from_uri: 'viking://resources/a b.md',
      to_uri: 'viking://resources/c d.md',
    })
  })
  it.each(['', '-r', '--recursive'])(
    'copies with an optional recursion flag: %s',
    (flag) => {
      expect(
        parseTransfer(
          `ov cp ${flag} viking://resources/a viking://resources/b`,
          'cp',
        ),
      ).toEqual({
        from_uri: 'viking://resources/a',
        to_uri: 'viking://resources/b',
        recursive: Boolean(flag),
      })
    },
  )
  it.each(['mv', 'cp'] as const)(
    'rejects missing, root, equal and extra %s targets',
    (command) => {
      for (const args of [
        '',
        'viking://resources/a',
        'viking:// viking://resources/b',
        'viking://resources/a /tmp/b',
        'viking://resources/a viking://resources/a/',
        'viking://resources/a viking://resources/b extra',
        'viking://resources/a viking://resources/b --force',
      ]) {
        expect(() => parseTransfer(`${command} ${args}`, command)).toThrow(
          `${command}Usage`,
        )
      }
    },
  )
  it('rejects recursion on mv and duplicate recursion on cp', () => {
    expect(() =>
      parseTransfer('/mv viking://resources/a viking://resources/b -r', 'mv'),
    ).toThrow('mvUsage')
    expect(() =>
      parseTransfer(
        '/cp -r viking://resources/a viking://resources/b --recursive',
        'cp',
      ),
    ).toThrow('cpUsage')
  })
})
