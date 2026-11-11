import type { TuiQuickCommand } from '@openviking/react/components/tui';

const COPY = {
  'en-US': {
    readme: 'README.md',
    memory:
      "Use the five-part structure 'Problem -> Current state -> Options -> Recommendation -> Risks' for review docs; prefer tables over prose",
    query: 'What is the value of OpenViking?',
  },
  'zh-CN': {
    readme: 'docs/repository/README_CN.md',
    memory:
      "评审文档默认用 '问题→现状→方案对比→推荐项→风险' 五段式；表格优先于文字",
    query: 'OpenViking 有什么价值？',
  },
};

/** Public examples aligned with the Volcengine host; selection never executes. */
export function createTuiQuickCommands(
  locale: keyof typeof COPY,
): readonly TuiQuickCommand[] {
  const text = COPY[locale];
  return [
    {
      name: 'add-resource',
      value: `ov add-resource https://github.com/volcengine/OpenViking/blob/main/${text.readme}`,
    },
    {
      name: 'add-skill',
      value:
        'ov add-skill https://github.com/volcengine/OpenViking/tree/skills/llm-wiki -p viking://agent/skills',
    },
    {
      name: 'add-memory',
      value: `ov add-memory "${text.memory}" --session-id ov-tui-demo`,
    },
    { name: 'find', value: `ov find "${text.query}"` },
    { name: 'compile', value: 'ov compile ' },
  ];
}
