import { Button } from '#demo-controls';
import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { FilePreview } from '@openviking/react/components/file-preview';
import type { FilePreviewFileTab } from '@openviking/react/components/file-preview';
import { getFileTypeIcon } from '@openviking/react/icons';
import '@openviking/react/components/file-preview/style.css';

import { image, root, resolveImage } from '#file-preview-console-data';

export default function FilePreviewConsole() {
  const zh = useLang() === 'zh';
  const [current, setCurrent] = useState('document');
  const [notice, setNotice] = useState('');
  const [draftContent, setDraftContent] = useState<string>();
  const Icon = getFileTypeIcon(current === 'image' ? 'cli.svg' : 'guide.md');
  const tab: FilePreviewFileTab = {
    key: current,
    kind: 'file',
    title: current === 'image' ? 'cli.svg' : 'guide.md',
    icon: <Icon width={14} height={14} />,
    source:
      current === 'image'
        ? { url: image, fileName: 'cli.svg', path: `${root}/cli.svg` }
        : {
            content: '# Guide',
            fileName: 'guide.md',
            path: `${root}/guide.md`,
          },
    levels:
      current === 'document'
        ? [
            {
              key: 'L0',
              label: zh ? 'L0 摘要' : 'L0 Abstract',
              source: {
                content: `${zh ? '# 快速开始\n\n导入资源并检索上下文。' : '# Quick start\n\nImport resources and search context.'}\n\n![CLI](./images/cli.svg)`,
                fileName: '.abstract.md',
                path: `${root}/.abstract.md`,
              },
            },
            {
              key: 'L1',
              label: zh ? 'L1 总览' : 'L1 Overview',
              source: {
                content: `${zh ? '# 使用总览' : '# Overview'}\n\n![CLI](../images/cli.svg)\n\n${zh ? '总览中的上一级相对路径图片。' : 'A parent-relative image in the overview.'}`,
                fileName: '.overview.md',
                path: `${root}/.overview.md`,
              },
            },
            {
              key: 'L2',
              editing: {
                save: (value) => {
                  setDraftContent(value);
                  return Promise.resolve();
                },
              },
              label: zh ? 'L2 正文' : 'L2 Content',
              source: {
                content:
                  draftContent ??
                  `---\ntitle: Guide\nversion: 1\n---\n\n${zh ? '# 完整正文' : '# Full content'}\n\n![CLI](viking://resources/manual/cli.svg)\n\n${zh ? '正文中的 Viking URI 图片。' : 'A Viking URI image in the full content.'}\n\n| Item | Status |\n| --- | --- |\n| Table | Ready |`,
                fileName: 'guide.md',
                path: `${root}/guide.md`,
              },
            },
          ]
        : undefined,
    resolveImage,
    operations: { openParent: async () => {} },
    onOpenParent: (uri) =>
      setNotice(`${zh ? '模拟导航' : 'Simulated navigation'}: ${uri}`),
    slots: {
      titleExtra: (
        <span className="ov-demo-tag">{zh ? '可管理' : 'Can manage'}</span>
      ),
      footer: (
        <>
          <span>{zh ? '更新于' : 'Updated'} 2026-11-09 10:00</span>
          <span>{zh ? '演示数据' : 'Demo data'}</span>
        </>
      ),
    },
  };
  return (
    <div>
      <div
        role="group"
        aria-label={zh ? '预览示例' : 'Preview examples'}
        style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}
      >
        {['document', 'image', 'empty', 'error'].map((item) => (
          <Button
            size="small"
            type={current === item ? 'primary' : 'secondary'}
            key={item}
            htmlType="button"
            className="ov-file-preview__button"
            aria-pressed={current === item}
            onClick={() => setCurrent(item)}
          >
            {
              {
                document: zh ? '分级内容' : 'Content levels',
                image: zh ? '原始图片' : 'Original image',
                empty: zh ? '空文件' : 'Empty file',
                error: zh ? '图片失败' : 'Image error',
              }[item]
            }
          </Button>
        ))}
      </div>
      <div className="ov-demo-surface" style={{ height: 680 }}>
        <FilePreview
          key={current}
          tabs={[
            current === 'empty'
              ? { ...tab, source: { content: '', fileName: 'empty.md' } }
              : current === 'error'
                ? {
                    ...tab,
                    source: {
                      url: 'data:image/png;base64,invalid',
                      fileName: 'broken.png',
                    },
                  }
                : tab,
          ]}
          locale={zh ? 'zh-CN' : 'en-US'}
          labels={{
            copyPath: zh ? '复制路径' : 'Copy path',
            fileEmpty: zh ? '文件为空' : 'This file is empty',
            unsupported: zh
              ? '暂不支持预览此文件'
              : 'Preview is not supported for this format',
          }}
        />
      </div>
      <p className="ov-demo-hint" role="status">
        {notice ||
          (zh
            ? '模拟数据：切换 L0/L1/L2，检查各级图片；原始图片支持点击放大。'
            : 'Fixture data: switch L0/L1/L2 to check images; click the original image to enlarge.')}
      </p>
    </div>
  );
}
