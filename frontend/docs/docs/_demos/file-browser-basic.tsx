import { DEMO_EXPERIENCE, DEMO_WIKI } from '#dashboard-demo-data';
import { Button } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { usePreviewControls } from '#file-browser-preview-controls';
import {
  DEMO_WIKI_FILE,
  DEMO_PREVIEW_FILE,
  MEMORIES,
} from '#file-browser-basic-data';
import '@openviking/react/modules/file-browser/style.css';

export default function FileBrowserBasic() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const preview = usePreviewControls(componentLocale);
  const text =
    componentLocale === 'zh-CN'
      ? {
          canonical: 'Wiki：规范 URI',
          alias: 'Wiki：~ 别名',
          import: '宿主已收到导入事件',
          hint: '打开搜索弹窗，输入 profile 体验离线搜索',
        }
      : {
          canonical: 'Wiki: canonical URI',
          alias: 'Wiki: ~ alias',
          import: 'Host received import',
          hint: 'Open search and submit profile to try offline search',
        };
  const [uri, setUri] = useState<string | null>(null);
  const [imported, setImported] = useState(false);
  return (
    <div
      lang={componentLocale}
      style={{
        height: 720,
        display: 'grid',
        gridTemplateRows: 'minmax(0, 1fr) auto',
        gap: 8,
      }}
    >
      <OpenVikingProvider fetch={preview.fetch}>
        <FileBrowser
          locale={componentLocale}
          uri={uri ?? ''}
          onUriChange={setUri}
          defaultExpandedUri={MEMORIES}
          agentConnect={{ apiKey: 'demo-api-key', locale: componentLocale }}
          onImportResources={() => setImported(true)}
          previewSlots={preview.previewSlots}
          previewActions={preview.previewActions}
          renderImportResources={(trigger) => (
            <span data-file-browser-import-trigger>{trigger}</span>
          )}
        />
      </OpenVikingProvider>
      <div className="ov-demo-controls">
        {preview.controls}
        <Button
          size="small"
          type="default"
          htmlType="button"
          onClick={() => setUri(DEMO_WIKI_FILE)}
        >
          {text.canonical}
        </Button>{' '}
        <Button
          size="small"
          type="default"
          htmlType="button"
          onClick={() => setUri('viking://~/resources/handbook/release.md')}
        >
          {text.alias}
        </Button>
        <Button
          size="small"
          type="default"
          onClick={() => setUri(DEMO_PREVIEW_FILE)}
        >
          {componentLocale === 'zh-CN'
            ? '预览可编辑文件'
            : 'Preview editable file'}
        </Button>{' '}
        <Button size="small" type="default" onClick={() => setUri(MEMORIES)}>
          {componentLocale === 'zh-CN'
            ? '预览目录摘要'
            : 'Preview directory summaries'}
        </Button>{' '}
        <Button size="small" onClick={() => setUri(DEMO_EXPERIENCE)}>
          {componentLocale === 'zh-CN' ? '预览经验文件' : 'Preview experience'}
        </Button>
        <Button size="small" onClick={() => setUri(DEMO_WIKI)}>
          {componentLocale === 'zh-CN'
            ? '预览编译目录'
            : 'Preview compile target'}
        </Button>
        <Button
          size="small"
          onClick={() =>
            setUri(
              `${DEMO_PREVIEW_FILE.slice(0, DEMO_PREVIEW_FILE.lastIndexOf('/'))}/cli.svg`,
            )
          }
        >
          {componentLocale === 'zh-CN' ? '预览图片' : 'Preview image'}
        </Button>
        <Button
          size="small"
          onClick={() =>
            setUri(
              `${DEMO_PREVIEW_FILE.slice(0, DEMO_PREVIEW_FILE.lastIndexOf('/'))}/demo.webm`,
            )
          }
        >
          {componentLocale === 'zh-CN' ? '预览视频' : 'Preview video'}
        </Button>
        <code style={{ overflowWrap: 'anywhere' }}>{uri}</code>
        <span role="status">{imported ? text.import : text.hint}</span>
      </div>
    </div>
  );
}
