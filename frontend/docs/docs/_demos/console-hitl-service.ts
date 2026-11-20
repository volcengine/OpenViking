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
  const titles = new Map<string, string>();
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
      const title = locale() === 'zh-CN' ? '整理 Wiki' : 'Organize a Wiki';
      history.set(id, []);
      titles.set(id, title);
      return Promise.resolve({ id, title });
    },
    loadMessages(id) {
      return Promise.resolve(history.get(id) ?? []);
    },
    listSessions() {
      return Promise.resolve([...titles].map(([id, title]) => ({ id, title })));
    },
    removeSession(id) {
      history.delete(id);
      titles.delete(id);
      return Promise.resolve();
    },
    renameSession(id, title) {
      titles.set(id, title);
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
            ? '已收到配置，将继续编译。（模拟结果）'
            : 'Configuration received. Continuing the compile task. (Simulated result)',
        };
        return;
      }
      const id = `demo-input-${++sequence}`;
      const report = /日报|daily report/i.test(input.text);
      const name = report ? 'demo-daily-report' : 'demo-wiki';
      const outputUri = `viking://resources/${name}`;
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
              card(
                'compile_source',
                zh ? '选择来源目录' : 'Choose source folders',
                {
                  suggested_values: [],
                },
              ),
              card('compile_name', zh ? '配置名称' : 'Configuration name', {
                suggested_value: name,
              }),
              card('compile_output_type', zh ? '产物类型' : 'Output type', {
                options: [
                  {
                    value: report ? 'markdown' : 'wiki',
                    label: report
                      ? 'Markdown'
                      : zh
                        ? 'Wiki（结构化知识库）'
                        : 'Wiki (structured knowledge base)',
                  },
                ],
              }),
              card('compile_target', zh ? '输出目录' : 'Output directory', {
                suggested_value: outputUri,
              }),
              card('compile_skill', zh ? '编译 Skill' : 'Compile Skill', {
                candidates: [
                  report
                    ? {
                        uri: 'viking://agent/skills/daily-report',
                        label: 'Daily report',
                      }
                    : {
                        uri: 'viking://agent/skills/wiki',
                        label: 'Wiki Skill',
                      },
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
                  frequency: report ? 'daily' : 'disabled',
                  time: '09:00',
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
