import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';
import { Button } from '#demo-controls';
import { usePreviewControls } from '#file-browser-preview-controls';
import { MEMORIES } from '#file-browser-basic-data';

const PROFILE = `${MEMORIES}/profile.md`;
import { Fault, RenderFailureDemo } from '#render-failure';

type Target = 'tree' | 'preview';

function Browser({
  locale,
  target,
}: {
  locale: 'zh-CN' | 'en-US';
  target: Target;
}) {
  const preview = usePreviewControls(locale);
  return (
    <OpenVikingProvider fetch={preview.fetch}>
      <div style={{ height: 480, display: 'flex' }}>
        <FileBrowser
          locale={locale}
          defaultExpandedUri={MEMORIES}
          uri={PROFILE}
          onImportResources={() => undefined}
          renderImportResources={(trigger) =>
            target === 'tree' ? <Fault>{trigger}</Fault> : trigger
          }
          previewSlots={{
            // 预览操作区由组件内的错误边界接住，和目录树是两个独立区域。
            actions:
              target === 'preview'
                ? () => (
                    <Fault>
                      <span />
                    </Fault>
                  )
                : undefined,
          }}
        />
      </div>
    </OpenVikingProvider>
  );
}

export default function FileBrowserCrash() {
  const zh = useLang() === 'zh';
  const locale = zh ? 'zh-CN' : 'en-US';
  const [target, setTarget] = useState<Target>('tree');
  return (
    <RenderFailureDemo
      hint={
        zh
          ? '两个区域各自隔离：目录树出错时预览区照常可用，预览操作区出错时目录树照常可用。'
          : 'Each region is isolated: when the tree fails the preview stays usable, and when the preview actions fail the tree stays usable.'
      }
    >
      <p className="ov-demo-controls">
        {(
          [
            ['tree', zh ? '目录树失败' : 'Tree failure'],
            ['preview', zh ? '预览操作区失败' : 'Preview actions failure'],
          ] as const
        ).map(([value, label]) => (
          <Button
            key={value}
            size="small"
            type={target === value ? 'primary' : 'secondary'}
            htmlType="button"
            aria-pressed={target === value}
            onClick={() => setTarget(value)}
          >
            {label}
          </Button>
        ))}
      </p>
      <Browser key={target} locale={locale} target={target} />
    </RenderFailureDemo>
  );
}
