import { Button } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { VikingBot } from '@openviking/react/components/viking-bot';
import type { VikingBotReference } from '@openviking/react/components/viking-bot';
import '@openviking/react/components/viking-bot/style.css';

const INITIAL: VikingBotReference[] = [
  { name: 'project', type: 'directory', uri: 'viking://resources/project' },
  {
    name: 'README.md',
    type: 'file',
    uri: 'viking://resources/project/README.md',
  },
  {
    name: 'README.md',
    type: 'file',
    uri: 'viking://resources/other-project/README.md',
  },
  {
    name: 'report.pdf',
    type: 'file',
    uri: 'viking://resources/project/report.pdf',
  },
  {
    name: 'archive',
    type: 'directory',
    uri: 'viking://resources/project/archive',
  },
  {
    name: 'notes.md',
    type: 'file',
    uri: 'viking://resources/project/archive/notes.md',
  },
  {
    name: 'data.json',
    type: 'file',
    uri: 'viking://resources/project/archive/data.json',
  },
];
const ROWS = INITIAL.map((item) => ({
  ...item,
  kind: 'entry' as const,
  id: item.uri,
  depth: 0,
}));

export default function VikingBotReferences() {
  const lang = useLang() === 'zh' ? 'zh' : 'en';
  const text = {
    zh: { reset: '重置示例引用' },
    en: { reset: 'Reset example references' },
  }[lang];
  const [selected, setSelected] =
    useState<readonly VikingBotReference[]>(INITIAL);
  const [draft, setDraft] = useState('');
  const [result, setResult] = useState('');
  return (
    <>
      <Button htmlType="button" onClick={() => setSelected(INITIAL)}>
        {text.reset}
      </Button>
      <div style={{ height: 390 }}>
        <VikingBot
          locale={lang === 'zh' ? 'zh-CN' : 'en-US'}
          messages={[]}
          emptyState={null}
          draft={draft}
          onDraftChange={setDraft}
          references={{ rows: ROWS, selected, onChange: setSelected }}
          onOpenUri={setResult}
          onSend={(value) => setResult(JSON.stringify(value, null, 2))}
        />
      </div>
      <output
        aria-live="polite"
        style={{
          display: 'block',
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
        }}
      >
        {result}
      </output>
    </>
  );
}
