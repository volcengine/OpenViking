import {
  cancelCompile,
  createCompile,
  fetchCompileTask,
  fetchCompileTasks,
} from './api'
import { parseCompile, tokenize } from './commands'

export async function runCompileCommand(
  input: string,
  key: string,
): Promise<{ body: string; taskId?: string }> {
  const tokens = tokenize(input)
  if (tokens[0] === 'compile') {
    const task = await createCompile(parseCompile(tokens), key)
    return {
      body: `${task.task_id}\n${task.status}\n${task.meta?.request?.to || ''}`,
      taskId: task.task_id,
    }
  }
  if (tokens[0] !== 'task') throw new Error('Unknown command')
  if (tokens[1] === 'status' || tokens[1] === 'cancel') {
    if (tokens.length !== 3) throw new Error('task status|cancel <task-id>')
    const task = await (
      tokens[1] === 'status' ? fetchCompileTask : cancelCompile
    )(tokens[2])
    return {
      body: `${task.task_id}\n${task.status}\n${task.stage || ''}\n${task.error || ''}`,
      taskId: task.task_id,
    }
  }
  if (tokens[1] === 'list') {
    let status = '',
      cursor: string | undefined
    for (let i = 2; i < tokens.length; i += 2) {
      if (!tokens[i + 1]) throw new Error('Missing flag value')
      if (tokens[i] === '--status') status = tokens[i + 1]
      else if (tokens[i] === '--cursor') cursor = tokens[i + 1]
      else if (tokens[i] !== '--task-type' || tokens[i + 1] !== 'compile')
        throw new Error(
          'Supported: --task-type compile --status <status> --cursor <cursor>',
        )
    }
    const page = await fetchCompileTasks(status, '', cursor)
    return {
      body:
        page.items.map((task) => `${task.task_id}  ${task.status}`).join('\n') +
        (page.next_cursor
          ? `\n\ntask list --task-type compile --cursor ${page.next_cursor}`
          : ''),
    }
  }
  throw new Error('task status|cancel|list')
}
