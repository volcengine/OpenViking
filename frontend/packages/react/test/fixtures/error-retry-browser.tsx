import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { FilePreview } from '@openviking/react/components/file-preview';
import {
  VikingBotComposer,
  VikingBotInteraction,
  VikingBotMessageList,
} from '@openviking/react/components/viking-bot';
import type {
  VikingBotComposerRef,
  VikingBotMessagePart,
} from '@openviking/react/components/viking-bot';
import '@openviking/react/components/file-preview/style.css';
import '@openviking/react/components/viking-bot/style.css';

let broken = false;
function Fault() {
  if (broken) throw new Error('intentional browser retry audit failure');
  return <span>Recovered region</span>;
}
const message = {
  id: 'bad',
  role: 'assistant' as const,
  parts: [{ type: 'text' as const, text: 'Original message' }],
};
const healthy = {
  id: 'healthy',
  role: 'system' as const,
  parts: [{ type: 'text' as const, text: 'Healthy sibling message' }],
};
const names = [
  'None',
  'Composer',
  'Interaction',
  'List footer',
  'Message',
  'Streaming',
  'FilePreview body',
  'FileViewer read',
];

function Audit() {
  const [name, select] = useState('None');
  const [, update] = useState(0);
  const [draft, setDraft] = useState('Composer draft');
  const composer = useRef<VikingBotComposerRef>(null);
  const slots = {
    errorFallback: (reset: () => void) => (
      <div role="alert">
        <p>Host fallback</p>
        <button type="button" onClick={reset}>
          Retry selected region
        </button>
      </div>
    ),
  };
  const streamingParts: VikingBotMessagePart[] = [
    {
      type: 'text',
      format: 'plain',
      get text() {
        if (broken) throw new Error('intentional streaming row failure');
        return 'Recovered streaming row';
      },
    },
  ];
  const content =
    name === 'Composer' ? (
      <VikingBotComposer
        ref={composer}
        slots={slots}
        value={draft}
        onValueChange={setDraft}
        actions={<Fault />}
      />
    ) : name === 'Interaction' ? (
      <VikingBotInteraction
        slots={slots}
        request={{ kind: 'custom', render: () => <Fault /> }}
      />
    ) : name === 'List footer' ? (
      <VikingBotMessageList
        slots={slots}
        messages={[healthy]}
        footer={<Fault />}
      />
    ) : name === 'Message' ? (
      <VikingBotMessageList
        slots={slots}
        messages={[message, healthy]}
        renderMessage={(item) =>
          item.id === 'bad' ? <Fault /> : <span>Healthy sibling message</span>
        }
      />
    ) : name === 'Streaming' ? (
      <VikingBotMessageList
        slots={slots}
        messages={[healthy]}
        streaming={{ iteration: 1, parts: streamingParts }}
      />
    ) : name === 'FilePreview body' ? (
      <FilePreview
        slots={slots}
        tabs={[
          {
            key: 'text',
            kind: 'file',
            source: {
              fileName: 'retry.txt',
              content: 'Recovered file preview',
            },
          },
        ]}
      />
    ) : name === 'FileViewer read' ? (
      <FilePreview
        slots={slots}
        tabs={[
          {
            key: 'read',
            kind: 'file',
            source: { fileName: 'retry.txt', url: '/read-retry.txt' },
            slots,
          },
        ]}
      />
    ) : (
      <p>Select an audit case.</p>
    );
  return (
    <main style={{ maxWidth: 920, margin: '24px auto', padding: 24 }}>
      <h1>Error fallback retry audit</h1>
      <label>
        Case{' '}
        <select
          aria-label="Audit case"
          value={name}
          onChange={(event) => {
            broken = false;
            select(event.target.value);
          }}
        >
          {names.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </label>
      <p>
        <label>
          Host draft <input aria-label="Host draft" defaultValue="" />
        </label>
      </p>
      <p>
        <button
          type="button"
          onClick={() => {
            broken = true;
            update((value) => value + 1);
          }}
        >
          Break selected region
        </button>{' '}
        <button
          type="button"
          onClick={() => {
            broken = false;
            update((value) => value + 1);
          }}
        >
          Resolve cause
        </button>{' '}
        <button
          type="button"
          onClick={() => composer.current?.setText('Imperative composer draft')}
        >
          Write through composer ref
        </button>
      </p>
      <p>
        Controlled composer draft: <output>{draft}</output>
      </p>
      <section
        key={name}
        aria-label="Audited region"
        style={{ height: 400, border: '1px solid currentColor' }}
      >
        {content}
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<Audit />);
