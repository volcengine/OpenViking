import { FilePreview } from '@openviking/react/components/file-preview';
import { createRoot } from 'react-dom/client';

const messages = [
  {
    id: 'msg-user',
    role: 'user',
    parts: [{ type: 'text', text: 'First line\nSecond line' }],
    created_at: '2026-11-14T03:29:18.902232+00:00',
  },
  {
    id: 'msg-assistant',
    role: 'assistant',
    parts: [{ type: 'text', text: 'Reply <script>safe text</script>' }],
    created_at: '2026-11-14T03:29:20Z',
  },
  {
    id: 'msg-context',
    role: 'user',
    parts: [
      {
        type: 'context',
        uri: 'viking://resources/guide.md',
        context_type: 'resource',
        abstract: 'Referenced guide\nSecond line',
      },
    ],
  },
  {
    id: 'msg-image',
    role: 'user',
    parts: [
      {
        type: 'image_url',
        image_url: { url: 'https://images.example/figure.png', detail: 'high' },
      },
    ],
  },
];
const content = messages.map((message) => JSON.stringify(message)).join('\n');
const path = 'viking://user/default/sessions/session-1/messages.jsonl';
globalThis.fetch = async () => new Response(content);

createRoot(document.getElementById('root')!).render(
  <>
    {['inline', 'remote', 'ordinary', 'unsupported'].map((id) => (
      <section id={id} key={id} style={{ height: 500 }}>
        <FilePreview
          locale="en-US"
          tabs={[
            {
              kind: 'file',
              key: id,
              source: {
                fileName: 'messages.jsonl',
                path:
                  id === 'ordinary'
                    ? 'viking://resources/sessions/tutorial/messages.jsonl'
                    : path,
                ...(id === 'remote'
                  ? { url: 'https://preview.test/session.jsonl' }
                  : {
                      content:
                        id === 'unsupported'
                          ? JSON.stringify({
                              role: 'assistant',
                              content: 'Keep generic JSON',
                              parts: [
                                { type: 'text', text: 'Keep generic JSON' },
                                {
                                  type: 'future_part',
                                  payload: 'Unsupported future content',
                                },
                              ],
                            })
                          : content,
                    }),
              },
            },
          ]}
        />
      </section>
    ))}
    <section id="json-preview" style={{ height: 500 }}>
      <FilePreview
        locale="en-US"
        tabs={[
          {
            kind: 'file',
            key: 'json-preview',
            source: {
              content: '{"outer":{"child":{"value":1},"sibling":2}}',
              fileName: 'data.json',
              path: 'viking://resources/data.json',
            },
          },
        ]}
      />
    </section>
  </>,
);
