import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { FilePreview } from '@openviking/react/components/file-preview';
import type {
  FilePreviewActions,
  FilePreviewTab,
} from '@openviking/react/components/file-preview';
import { getFileTypeIcon } from '@openviking/react/icons';
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

See [the compile notes](viking://system/compile/notes.md) and [a relative page](./sibling.md).
`,
    dashboard:
      'A content tab renders whatever the host passes — a dashboard, a graph, a summary. The component does not know what it is.',
    dashboardTitle: 'Dashboard',
    hint: 'Switch tabs, copy the path or the content, or toggle preview and source.',
    contentCopied: 'Content copied',
    pathCopied: 'Path copied',
    permissionTag: 'Can manage',
    parentOpened: (path: string) => `Simulated parent navigation: ${path}`,
    linkOpened: (uri: string) => `Simulated link navigation: ${uri}`,
    saved: 'Content saved',
    cancelled: 'Editing cancelled',
    previewLabel: 'File preview',
    labels: {
      copyPath: 'Copy path',
    },
    // 动作按钮由示例自己渲染，文案也归示例，不经过组件的 labels。
    actions: {
      copyContent: 'Copy content',
      preview: 'Preview',
      source: 'Source',
      edit: 'Edit',
      save: 'Save',
      cancel: 'Cancel',
    },
  },
  zh: {
    markdown: `# LLM Wiki

把异构的知识来源编译成可长期使用、彼此关联的知识。

## 目标

把素材变成可复用的页面，而不是一堆摘要。

参见 [编译说明](viking://system/compile/notes.md) 与 [同目录页面](./sibling.md)。
`,
    dashboard:
      '内容页签原样渲染宿主传入的节点——可以是看板、图谱或摘要，组件不关心它是什么。',
    dashboardTitle: '看板',
    hint: '切换页签、复制路径或正文，或在预览与源码之间切换。',
    contentCopied: '正文已复制',
    pathCopied: '路径已复制',
    permissionTag: '可管理',
    parentOpened: (path: string) => `已模拟跳转到上级：${path}`,
    linkOpened: (uri: string) => `已模拟打开链接：${uri}`,
    saved: '正文已保存',
    cancelled: '已取消编辑',
    previewLabel: '文件预览',
    labels: {
      copyPath: '复制路径',
    },
    actions: {
      copyContent: '复制正文',
      preview: '预览',
      source: '源码',
      edit: '编辑',
      save: '保存',
      cancel: '取消',
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

  // 图标由宿主决定：组件不按文件名推断类型，这里用包的图标集按格式取一个。
  // The host decides the icon: the component never infers a type from the file
  // name, so the demo picks one per format from the package icon set.
  const tabIcon = (fileName: string) => {
    const Icon = getFileTypeIcon(fileName);
    return <Icon width={14} height={14} />;
  };

  // 组件不内置复制、下载、视图切换与编辑按钮，这些由宿主用 `actions` 上下文
  // 自己渲染。示例演示一个最小实现。
  // The component ships no copy, download, view-switch or edit buttons; the host
  // renders them from the `actions` context. This is a minimal implementation.
  const renderActions = (actions: FilePreviewActions) => (
    <>
      {actions.canCopyContent ? (
        <button
          type="button"
          className="ov-file-preview__button"
          disabled={actions.busy}
          onClick={() => void actions.copyContent()}
        >
          {t.actions.copyContent}
        </button>
      ) : null}
      {actions.canSwitchView ? (
        <div className="ov-file-preview__switch" role="group">
          {(['preview', 'source'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              className="ov-file-preview__switch-item"
              data-active={actions.viewMode === mode || undefined}
              aria-pressed={actions.viewMode === mode}
              disabled={actions.busy}
              onClick={() => actions.setViewMode(mode)}
            >
              {mode === 'preview' ? t.actions.preview : t.actions.source}
            </button>
          ))}
        </div>
      ) : null}
      {actions.canEdit && !actions.editing ? (
        <button
          type="button"
          className="ov-file-preview__button"
          disabled={actions.busy}
          onClick={actions.startEditing}
        >
          {t.actions.edit}
        </button>
      ) : null}
      {actions.editing ? (
        <>
          <button
            type="button"
            className="ov-file-preview__button"
            disabled={actions.busy}
            onClick={actions.cancelEditing}
          >
            {t.actions.cancel}
          </button>
          <button
            type="button"
            className="ov-file-preview__button ov-file-preview__button--primary"
            disabled={actions.busy}
            onClick={() => void actions.saveEditing()}
          >
            {t.actions.save}
          </button>
        </>
      ) : null}
    </>
  );

  const tabs: [FilePreviewTab, ...FilePreviewTab[]] = [
    {
      kind: 'file',
      key: 'file',
      title: 'SKILL.md',
      icon: tabIcon('SKILL.md'),
      source: {
        content,
        fileName: 'SKILL.md',
        path: 'viking://system/compile/llm-wiki/SKILL.md',
      },
      onCopyContent: () => setActiveHint(t.contentCopied),
      onCopyPath: () => setActiveHint(t.pathCopied),
      operations: {
        openParent: async () => {},
        // 正文里的链接交给宿主解析；组件把 Markdown 里的原始目标原样传出来。
        openUri: async () => {},
      },
      onOpenParent: (path) => setActiveHint(t.parentOpened(path)),
      onOpenUri: (uri) => setActiveHint(t.linkOpened(uri)),
      editing: {
        save: (value) => {
          setContent(value);
          return Promise.resolve();
        },
        onSave: () => setActiveHint(t.saved),
        onCancel: () => setActiveHint(t.cancelled),
      },
      slots: {
        actions: renderActions,
        // 路径旁的附加标记由宿主决定含义与样式，组件只负责位置。
        titleExtra: <span className="ov-demo-tag">{t.permissionTag}</span>,
      },
    },
    {
      kind: 'file',
      key: 'diagram',
      title: 'diagram.svg',
      icon: tabIcon('diagram.svg'),
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
      icon: tabIcon(''),
      content: dashboard,
    },
  ];

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {/*
       * 自己再包一层卡片：Rspress 的预览卡片带 16px 内边距，组件贴不到边，
       * 看上去像是组件内部留了白。这里用负外边距抵消掉那 16px，再按宿主的
       * 做法给卡片加边框与圆角，组件就与卡片边缘齐平——哪些留白属于组件、
       * 哪些属于示例一眼可分。
       *
       * A card of our own. Rspress's preview card carries 16px of padding, so the
       * component cannot reach the edge and the gap reads as its own. Negative
       * margins cancel that padding and the card gets its own border and radius,
       * matching the host, so the component sits flush and it is obvious which
       * spacing belongs to which.
       */}
      <div className="ov-demo-surface" style={{ height: 560 }}>
        <FilePreview
          tabs={tabs}
          locale={lang === 'zh' ? 'zh-CN' : 'en-US'}
          labels={t.labels}
          aria-label={t.previewLabel}
        />
      </div>
      {/* 示例自己的说明与操作回执，不属于组件。用主题变量而不是写死灰值，
          暗色下才可读。 */}
      <p className="ov-demo-hint">{activeHint || t.hint}</p>
    </div>
  );
}
