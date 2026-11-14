import type {
  VikingBotChatService,
  VikingBotSessionService,
} from '@openviking/react/modules/console';
import type { VikingBotMessage } from '@openviking/react/components/viking-bot';

export function createHitlDemo(
  locale: () => 'zh-CN' | 'en-US',
  fails: () => boolean,
): VikingBotChatService & VikingBotSessionService {
  const history = new Map<string, VikingBotMessage[]>();
  let sequence = 0;
  const pause = (signal: AbortSignal) =>
    new Promise<void>((resolve, reject) => {
      signal.throwIfAborted();
      const abort = () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      const timer = setTimeout(() => {
        signal.removeEventListener('abort', abort);
        resolve();
      }, 400);
      signal.addEventListener('abort', abort, { once: true });
    });
  return {
    createSession() {
      const id = `demo-${++sequence}`;
      history.set(id, []);
      return Promise.resolve({ id, title: 'HITL demo' });
    },
    loadMessages(id) {
      return Promise.resolve(history.get(id) ?? []);
    },
    listSessions() {
      return Promise.resolve(
        [...history.keys()].map((id) => ({ id, title: 'HITL demo' })),
      );
    },
    removeSession(id) {
      history.delete(id);
      return Promise.resolve();
    },
    saveMessage(id, message) {
      history.get(id)?.push(message);
      return Promise.resolve();
    },
    async *stream(input, signal) {
      const zh = locale() === 'zh-CN';
      await pause(signal);
      if (input.text.startsWith('{"type":"request_user_input_response"')) {
        if (fails())
          throw new Error(zh ? '模拟发送失败' : 'Simulated send failure');
        yield {
          type: 'complete',
          text: zh
            ? '已收到配置，将继续生成 Wiki。（模拟结果）'
            : 'Configuration received. Continuing the Wiki task. (Simulated result)',
        };
        return;
      }
      const id = `demo-input-${++sequence}`;
      const card = (
        card_type: string,
        title: string,
        payload: Record<string, unknown>,
      ) => ({ card_type, title, payload });
      yield {
        type: 'tool',
        part: {
          type: 'tool',
          name: 'request_user_input',
          server: 'openviking-mcp',
          id,
          status: 'running',
        },
      };
      await pause(signal);
      yield {
        type: 'tool-result',
        id,
        output: JSON.stringify({
          structuredContent: {
            schema_version: '1.0',
            cards: [
              card('compile_name', zh ? '配置名称' : 'Configuration name', {
                suggested_value: 'demo-wiki',
              }),
              card('compile_output_type', zh ? '产物类型' : 'Output type', {
                options: [
                  {
                    value: 'wiki',
                    label: zh
                      ? 'Wiki（结构化知识库）'
                      : 'Wiki (structured knowledge base)',
                  },
                ],
              }),
              card('compile_target', zh ? '输出目录' : 'Output directory', {
                suggested_value: 'viking://resources/wiki',
              }),
              card('compile_skill', zh ? '编译 Skill' : 'Compile Skill', {
                candidates: [
                  { uri: 'viking://skills/wiki', label: 'Wiki Skill' },
                ],
              }),
              card(
                'compile_instruction',
                zh ? '补充指令' : 'Additional instructions',
                {
                  current_value: zh ? '保留来源引用' : 'Keep source citations',
                },
              ),
              card('compile_schedule', zh ? '自动更新' : 'Automatic updates', {
                initial_value: {
                  frequency: 'weekly',
                  weekday: 'wednesday',
                  time: '12:27',
                  timezone: 'Asia/Shanghai',
                },
              }),
            ],
          },
        }),
      };
      yield {
        type: 'complete',
        text: zh
          ? '请逐项确认编译配置。（模拟问题）'
          : 'Confirm each compile setting. (Simulated questions)',
      };
    },
  };
}
