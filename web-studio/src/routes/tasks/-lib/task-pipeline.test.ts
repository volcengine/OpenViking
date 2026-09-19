import { describe, expect, it } from 'vitest'

import en from '#/i18n/locales/en/workspace'
import zh from '#/i18n/locales/zh-CN/workspace'
import { getTaskPipelineGroups, getTaskPipelineSteps } from './task-pipeline'
import type { PipelineTranslate } from './task-pipeline'
import type { TaskRecord } from './task-record'

const TYPES = [
  'session_commit',
  'admin_reindex',
  'snapshot_restore_reindex',
  'connector_import',
  'add_resource',
  'add_skill',
]

function taskOf(task_type: string): TaskRecord {
  return {
    task_id: 't',
    task_type,
    status: 'running',
    result: {},
  } as TaskRecord
}

function namesOf(task: TaskRecord, t?: PipelineTranslate): string[] {
  const steps = getTaskPipelineSteps(task, t).map((s) => s.name)
  const groups = getTaskPipelineGroups(task, t).flatMap((g) =>
    g.type === 'serial' ? [g.step.name] : g.steps.map((s) => s.name),
  )
  return [...steps, ...groups]
}

describe('task pipeline labels', () => {
  it.each([
    ['en', en.tasksPage],
    ['zh-CN', zh.tasksPage],
  ])('resolves every step key to %s text', (_lng, namespace) => {
    const t = (key: string) =>
      key.split('.').reduce<any>((node, part) => node?.[part], namespace) ?? key

    for (const type of TYPES) {
      for (const name of namesOf(taskOf(type), t)) {
        // A key missing from the namespace comes back unresolved as "pipeline.step.<key>".
        expect(name).not.toMatch(/^pipeline\./)
        expect(name).not.toBe('')
      }
    }
  })

  it('falls back to the raw key when no translator is supplied', () => {
    expect(namesOf(taskOf('session_commit'))).toContain('sessionCommit')
  })
})
