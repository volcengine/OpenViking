import type {
  ConsoleTaskService,
  ConsoleTaskSnapshot,
  VikingBotChatService,
  VikingBotSessionService,
} from '@openviking/react/modules/console';

export function createTaskDemo() {
  let sequence = 0;
  let current: string | undefined;
  const tasks = new Map<string, ConsoleTaskSnapshot>();
  const create = () => {
    const id = `demo-task-${++sequence}`;
    tasks.set(id, {
      id,
      kind: 'resource',
      status: 'running',
      uri: 'viking://resources/demo',
    });
    current = id;
    return id;
  };
  const fetch: typeof globalThis.fetch = () =>
    Promise.resolve(
      Response.json({
        status: 'ok',
        result: {
          task_id: create(),
          status: 'success',
          root_uri: 'viking://resources/demo',
        },
      }),
    );
  const taskService: ConsoleTaskService = {
    getTask(id, signal) {
      signal.throwIfAborted();
      const task = tasks.get(id);
      if (!task) throw new Error('Demo task not found');
      return Promise.resolve({ ...task });
    },
  };
  const service: VikingBotChatService & VikingBotSessionService = {
    createSession: () => Promise.resolve({ id: 'demo-session', title: 'Demo' }),
    listSessions: () => Promise.resolve([]),
    loadMessages: () => Promise.resolve([]),
    removeSession: () => Promise.resolve(),
    async *stream() {
      await Promise.resolve();
      const id = current ?? create();
      yield {
        type: 'tool',
        part: {
          type: 'tool',
          id: 'import-call',
          name: 'add-resource',
          status: 'running',
        },
      };
      yield {
        type: 'tool-result',
        id: 'import-call',
        output: JSON.stringify({ task_id: id }),
        tasks: [{ id, kind: 'resource' }],
      };
      yield { type: 'complete', text: id };
    },
  };
  return {
    fetch,
    taskService,
    service,
    finish(failed: boolean) {
      if (!current) return;
      tasks.set(current, {
        ...tasks.get(current)!,
        status: failed ? 'failed' : 'completed',
        error: failed ? 'Demo: source parsing failed' : undefined,
      });
    },
  };
}
