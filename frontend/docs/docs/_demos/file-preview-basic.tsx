import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { FilePreview } from '@openviking/react/components/file-preview';
import type { FilePreviewTab } from '@openviking/react/components/file-preview';
import '@openviking/react/components/file-preview/style.css';

/**
 * 示例文案。文档站中英文页面共用这份示例，因此文案跟随页面语言。
 *
 * Demo copy. Both language pages share this demo, so the copy follows the page
 * language.
 */
const COPY = {
  en: {
    markdown: `# LLM Wiki

Compile heterogeneous knowledge sources into durable, connected knowledge.

## Objective

Turn the supplied sources into reusable pages instead of a pile of summaries.
`,
    dashboard:
      'A content tab renders whatever the host passes — a dashboard, a graph, a summary. The component does not know what it is.',
    dashboardTitle: 'Dashboard',
    hint: 'Switch tabs, copy the path or the content, or toggle preview and source.',
    contentCopied: 'Content copied',
    pathCopied: 'Path copied',
    parentOpened: (path: string) => `Simulated parent navigation: ${path}`,
    saved: 'Content saved',
    cancelled: 'Editing cancelled',
    previewLabel: 'File preview',
    labels: {
      copyContent: 'Copy content',
      copyPath: 'Copy path',
      edit: 'Edit',
      save: 'Save',
      cancel: 'Cancel',
      preview: 'Preview',
      source: 'Source',
    },
  },
  zh: {
    markdown: `# LLM Wiki

把异构的知识来源编译成可长期使用、彼此关联的知识。

## 目标

把素材变成可复用的页面，而不是一堆摘要。
`,
    dashboard:
      '内容页签原样渲染宿主传入的节点——可以是看板、图谱或摘要，组件不关心它是什么。',
    dashboardTitle: '看板',
    hint: '切换页签、复制路径或正文，或在预览与源码之间切换。',
    contentCopied: '正文已复制',
    pathCopied: '路径已复制',
    parentOpened: (path: string) => `已模拟跳转到上级：${path}`,
    saved: '正文已保存',
    cancelled: '已取消编辑',
    previewLabel: '文件预览',
    labels: {
      copyContent: '复制正文',
      copyPath: '复制路径',
      edit: '编辑',
      save: '保存',
      cancel: '取消',
      preview: '预览',
      source: '源码',
    },
  },
};

const diagramSvg =
  'data:image/svg+xml;charset=utf-8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="140">' +
      '<rect width="240" height="140" fill="#eef2f8"/>' +
      '<rect x="20" y="24" width="80" height="36" rx="6" fill="#2563c5"/>' +
      '<rect x="140" y="24" width="80" height="36" rx="6" fill="#7aa7e8"/>' +
      '<rect x="80" y="88" width="80" height="30" rx="6" fill="#a9c4ef"/>' +
      '</svg>',
  );

export default function FilePreviewBasic() {
  const lang = useLang() === 'zh' ? 'zh' : 'en';
  const t = COPY[lang];
  const [activeHint, setActiveHint] = useState('');
  // 正文按语言分别保存：切换语言时展示该语言的初始正文，编辑过的内容不丢。
  const [drafts, setDrafts] = useState<Partial<Record<string, string>>>({});
  const content = drafts[lang] ?? t.markdown;
  const setContent = (value: string) =>
    setDrafts((current) => ({ ...current, [lang]: value }));

  const dashboard = (
    <div style={{ padding: 16, color: '#8f959e', fontSize: 13 }}>
      {t.dashboard}
    </div>
  );

  const tabs: [FilePreviewTab, ...FilePreviewTab[]] = [
    {
      kind: 'file',
      key: 'file',
      title: 'SKILL.md',
      source: {
        content,
        fileName: 'SKILL.md',
        path: 'viking://system/compile/llm-wiki/SKILL.md',
      },
      onCopyContent: () => setActiveHint(t.contentCopied),
      onCopyPath: () => setActiveHint(t.pathCopied),
      operations: { openParent: async () => {} },
      onOpenParent: (path) => setActiveHint(t.parentOpened(path)),
      editing: {
        save: (value) => {
          setContent(value);
          return Promise.resolve();
        },
        onSave: () => setActiveHint(t.saved),
        onCancel: () => setActiveHint(t.cancelled),
      },
    },
    {
      kind: 'file',
      key: 'diagram',
      title: 'diagram.svg',
      source: {
        url: diagramSvg,
        fileName: 'diagram.svg',
        path: 'viking://system/compile/llm-wiki/diagram.svg',
      },
    },
    {
      kind: 'content',
      key: 'dashboard',
      title: t.dashboardTitle,
      content: dashboard,
    },
  ];

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <div style={{ height: 560 }}>
        <FilePreview
          tabs={tabs}
          locale={lang === 'zh' ? 'zh-CN' : 'en-US'}
          labels={t.labels}
          aria-label={t.previewLabel}
        />
      </div>
      <p style={{ margin: 0, color: '#8f959e', fontSize: 12 }}>
        {activeHint || t.hint}
      </p>
    </div>
  );
}
