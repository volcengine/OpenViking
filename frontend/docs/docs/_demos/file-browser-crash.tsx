import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';
import { usePreviewControls } from '#file-browser-preview-controls';
import { MEMORIES } from '#file-browser-basic-data';
import { Fault, RenderFailureDemo } from '#render-failure';

export default function FileBrowserCrash() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const preview = usePreviewControls(locale);
  return (
    <RenderFailureDemo
      hint={
        locale === 'zh-CN'
          ? '宿主包装的导入入口出错时，只有目录树一侧显示兜底，预览区照常可用。'
          : 'When the host-wrapped import entry fails, only the tree side shows the fallback; the preview stays usable.'
      }
    >
      <OpenVikingProvider fetch={preview.fetch}>
        <div style={{ height: 480, display: 'flex' }}>
          <FileBrowser
            locale={locale}
            defaultExpandedUri={MEMORIES}
            onImportResources={() => undefined}
            renderImportResources={(trigger) => <Fault>{trigger}</Fault>}
          />
        </div>
      </OpenVikingProvider>
    </RenderFailureDemo>
  );
}
