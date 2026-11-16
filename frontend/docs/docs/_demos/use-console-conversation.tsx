import { useState } from 'react';
import type {
  VikingBotAttachment,
  VikingBotReference,
  VikingBotSession,
  VikingBotProps,
} from '@openviking/react/components/viking-bot';
import { VikingBotIcon } from '@openviking/react/icons';

/** Fixed offline data; the host owns real conversations. */
export function useConsoleConversation(zh: boolean): Partial<VikingBotProps> {
  const [draft, setDraft] = useState('');
  const [selected, setSelected] = useState<readonly VikingBotReference[]>([]);
  const [attachments, setAttachments] = useState<
    readonly VikingBotAttachment[]
  >([]);
  const [sessions] = useState<readonly VikingBotSession[]>([
    {
      id: 'session_20261109030400_6c4ebf',
      title: zh ? '整理项目资料' : 'Organize project resources',
      updatedAt: '2026-11-09T03:04:00Z',
    },
    {
      id: 'docs-report',
      title: zh ? '查看日报内容' : 'Review daily report',
      updatedAt: '2026-11-08T09:16:00Z',
    },
  ]);
  const [currentId, setCurrentId] = useState<string>();
  const [expanded, setExpanded] = useState(true);
  const reset = (id?: string) => {
    setCurrentId(id);
    setDraft('');
    setAttachments([]);
    setSelected([]);
  };
  const prompts: readonly [string, string] = zh
    ? [
        '将选中的目录中的文件整理成 Wiki，保留来源和文档之间的关联',
        '基于选中的目录生成日报，并设置定时更新',
      ]
    : [
        'Organize files in the selected directory into a Wiki, preserving sources and relationships.',
        'Create a daily report from the selected directory and schedule updates.',
      ];
  return {
    messages:
      currentId === 'session_20261109030400_6c4ebf'
        ? [
            {
              id: 'demo-user',
              role: 'user',
              parts: [{ type: 'text', text: prompts[0] }],
            },
            {
              id: 'demo-assistant',
              role: 'assistant',
              parts: [
                {
                  type: 'tool',
                  id: 'list',
                  name: 'List files',
                  status: 'completed',
                  durationMs: 5000,
                },
                {
                  type: 'text',
                  text: zh
                    ? '我已查看目录中的资料。接下来会创建 Wiki 索引，保留来源和文档关联。'
                    : 'I checked the directory. Next I will create a Wiki index and preserve the source links.',
                },
                {
                  type: 'tool',
                  id: 'index',
                  name: 'Write Wiki index',
                  status: 'completed',
                  durationMs: 9000,
                },
                {
                  type: 'text',
                  text: zh
                    ? 'Wiki 索引已生成，可以从目录查看。'
                    : 'The Wiki index is ready to view in the directory.',
                },
              ],
            },
          ]
        : [],
    draft,
    onDraftChange: setDraft,
    attachments,
    onFilesAdded: (files) =>
      setAttachments((items) => [
        ...items,
        ...files.map((file, index) => ({
          key: `${file.name}-${file.lastModified}-${Date.now()}-${index}`,
          name: file.name,
          sizeBytes: file.size,
          mimeType: file.type,
          status: 'pending' as const,
        })),
      ]),
    onAttachmentRemove: (item) =>
      setAttachments((items) =>
        items.filter((entry) => entry.key !== item.key),
      ),
    quickInputs: [
      {
        id: 'wiki',
        label: zh ? '创建 Wiki' : 'Create Wiki',
        prompt: prompts[0],
        icon: <VikingBotIcon name="book" viewBox="-0.833333 -2.357143 16 16" />,
      },
      {
        id: 'report',
        label: zh ? '定时日报' : 'Scheduled report',
        prompt: prompts[1],
        icon: <VikingBotIcon name="report" />,
      },
    ],
    sessions: {
      items: sessions,
      currentId,
      currentUri: currentId ? `viking://session/${currentId}` : undefined,
      // 与内置对话一致：新建只清空当前会话，真正的会话在首次发送时才创建。
      // Like the built-in conversation, starting anew only clears the current
      // session; a real session is created on the first send.
      onNew: () => reset(),
      onSelect: reset,
    },
    references: {
      selected,
      onChange: setSelected,
      onExpand: () => setExpanded((value) => !value),
      rows: [
        {
          kind: 'entry',
          id: 'demo',
          depth: 0,
          uri: 'viking://resources/demo',
          name: 'demo',
          type: 'directory',
          expanded,
          hasChildren: true,
        },
        ...(expanded
          ? [
              {
                kind: 'entry' as const,
                id: 'notes',
                depth: 1,
                uri: 'viking://resources/demo/notes.md',
                name: 'notes.md',
                type: 'file' as const,
              },
            ]
          : []),
      ],
    },
  };
}
