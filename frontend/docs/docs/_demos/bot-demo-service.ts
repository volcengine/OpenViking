import type {
  VikingBotChatService,
  VikingBotSessionService,
} from '@openviking/react/modules/viking-bot';
import type { VikingBotMessage } from '@openviking/react/components/viking-bot';

/** Fixed public data; this adapter never accesses a real server. */
export function botDemoService(
  edition: 'native' | 'commercial' = 'native',
): VikingBotChatService & VikingBotSessionService {
  let active = false;
  const inputs: string[] = [];
  const sessions = new Map<string, VikingBotMessage[]>();
  const wait = (signal: AbortSignal) =>
    new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      const timer = setTimeout(() => {
        signal.removeEventListener('abort', abort);
        resolve();
      }, 650);
      if (signal.aborted) abort();
      else signal.addEventListener('abort', abort, { once: true });
    });
  return {
    createSession() {
      const id = `demo-${sessions.size + 1}`;
      sessions.set(id, []);
      return Promise.resolve({ id, title: id });
    },
    listSessions() {
      return Promise.resolve(
        [...sessions.keys()].map((id) => ({ id, title: id })),
      );
    },
    removeSession(id) {
      sessions.delete(id);
      return Promise.resolve();
    },
    loadMessages(id) {
      return Promise.resolve(sessions.get(id) ?? []);
    },
    saveMessage(id, message) {
      sessions.get(id)?.push(message);
      return Promise.resolve();
    },
    interrupt:
      edition === 'native'
        ? () => Promise.resolve({ interrupted: true })
        : undefined,
    steer:
      edition === 'native'
        ? (input) => {
            if (active) inputs.push(input.text);
            return Promise.resolve({ accepted: active });
          }
        : undefined,
    async *stream(input, signal) {
      if (input.text === 'error')
        throw new Error('Simulated service failure / 模拟服务错误');
      active = true;
      try {
        yield { type: 'iteration', iteration: 1 };
        yield {
          type: 'reasoning',
          id: 'thinking',
          text: 'Check the referenced material.\n核对引用资料。',
          delta: true,
        };
        await wait(signal);
        yield {
          type: 'tool',
          part: {
            type: 'tool',
            id: 'read',
            name: 'read',
            input: { uri: 'viking://resources/demo/notes.md' },
            status: 'running',
          },
        };
        await wait(signal);
        yield {
          type: 'tool-result',
          id: 'read',
          output: 'OpenViking demo notes.',
        };
        yield {
          type: 'reasoning',
          id: 'summary',
          text: 'Prepare the summary.\n整理结论。',
          delta: false,
        };
        await wait(signal);
        yield {
          type: 'text',
          text: 'OpenViking keeps resources, memories and skills in a shared context.\n\nOpenViking 将资源、记忆与技能保存在统一上下文中。',
          delta: true,
        };
        let iteration = 1;
        while (inputs.length) {
          const text = inputs.shift()!;
          yield { type: 'iteration', iteration: ++iteration };
          yield {
            type: 'reasoning',
            text: 'Consider the additional input.\n处理追加输入。',
            delta: false,
          };
          await wait(signal);
          yield {
            type: 'text',
            text: `Follow-up received / 已收到追问：${text}`,
            delta: true,
          };
        }
        yield { type: 'complete' };
      } finally {
        active = false;
        inputs.length = 0;
      }
    },
  };
}
