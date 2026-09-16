import { isOvClientError } from '#/lib/ov-client'
import {
  lookupSubmission,
  cancelCompile,
  createCompile,
  fetchCompileTask,
  fetchCompileTasks,
} from './api'
import { isRejectedCompileSubmission } from './errors'
import { CompileCommandError, parseCompile, tokenize } from './commands'

export async function runCompileCommand(
  input: string,
  key: string,
  localizeStatus: (status: string) => string = (status) => status,
  localizeStage: (stage: string) => string = (stage) => stage,
): Promise<{ body: string; taskId?: string }> {
  const tokens = tokenize(input)
  if (tokens[0] === 'compile') {
    const task = await createCompile(parseCompile(tokens), key)
    return {
      body: `${task.task_id}\n${localizeStatus(task.status || 'unknown')}\n${task.meta?.request?.to || ''}`,
      taskId: task.task_id,
    }
  }
  if (tokens[0] !== 'task') throw new CompileCommandError('unknownCommand')
  if (tokens[1] === 'status' || tokens[1] === 'cancel') {
    if (tokens.length !== 3) throw new CompileCommandError('taskUsage')
    const task = await (
      tokens[1] === 'status' ? fetchCompileTask : cancelCompile
    )(tokens[2])
    return {
      body: `${task.task_id}\n${localizeStatus(task.status || 'unknown')}\n${localizeStage(task.stage || '')}\n${task.error || ''}`,
      taskId: task.task_id,
    }
  }
  if (tokens[1] === 'list') {
    let status = '',
      cursor: string | undefined
    for (let i = 2; i < tokens.length; i += 2) {
      if (!tokens[i + 1]) throw new CompileCommandError('missingValue')
      if (tokens[i] === '--status') status = tokens[i + 1]
      else if (tokens[i] === '--cursor') cursor = tokens[i + 1]
      else if (tokens[i] !== '--task-type' || tokens[i + 1] !== 'compile')
        throw new CompileCommandError('listUsage')
    }
    const page = await fetchCompileTasks(status, '', cursor)
    return {
      body:
        page.items
          .map(
            (task) =>
              `${task.task_id}  ${localizeStatus(task.status || 'unknown')}`,
          )
          .join('\n') +
        (page.next_cursor
          ? `\n\ntask list --task-type compile${status ? ` --status ${status}` : ''} --cursor ${page.next_cursor}`
          : ''),
    }
  }
  throw new CompileCommandError('taskUsage')
}

export type PendingCompileSubmission = { raw: string; key: string }

/** Task queries must not consume an unresolved creation's idempotency key. */
export async function runCompileSubmission(
  input: string,
  pending: { current: PendingCompileSubmission | null },
  localizeStatus: (status: string) => string,
  saveKey: (key: string | null) => void,
  readKey: () => string | null = () => null,
  localizeStage: (stage: string) => string = (stage) => stage,
) {
  const tokens = tokenize(input)
  if (tokens[0] !== 'compile')
    return runCompileCommand(input, '', localizeStatus, localizeStage)
  parseCompile(tokens)
  // A remounted terminal has no private request in memory. Resolve the saved
  // submission before interpreting the command as a new creation.
  const restoredKey = pending.current ? null : readKey()
  if (restoredKey) {
    try {
      const task = await lookupSubmission(restoredKey)
      saveKey(null)
      return {
        body: `${task.task_id}\n${localizeStatus(task.status || 'unknown')}\n${task.meta?.request?.to || ''}`,
        taskId: task.task_id,
      }
    } catch (error) {
      if (!isOvClientError(error) || error.code !== 'NOT_FOUND') throw error
    }
    const submittedAt = Number(restoredKey.split(':')[0])
    if (!Number.isFinite(submittedAt) || Date.now() - submittedAt > 86400000)
      throw new CompileCommandError('expiredSubmission')
  }
  if (pending.current && pending.current.raw !== input)
    throw new CompileCommandError('pendingSubmission')
  const submission = pending.current ?? {
    raw: input,
    key: restoredKey || `${Date.now()}:${crypto.randomUUID()}`,
  }
  pending.current = submission
  saveKey(submission.key)
  try {
    const result = await runCompileCommand(
      input,
      submission.key,
      localizeStatus,
    )
    if (pending.current === submission) {
      pending.current = null
      saveKey(null)
    }
    return result
  } catch (error) {
    if (
      pending.current === submission &&
      isRejectedCompileSubmission(error) &&
      !restoredKey
    ) {
      pending.current = null
      saveKey(null)
    }
    // A restored request may differ from the original. Keep its key so a
    // conflict cannot turn the next correction into a duplicate creation.
    if (restoredKey && pending.current === submission) pending.current = null
    throw error
  }
}
