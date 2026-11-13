import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { FilePreview } from '@openviking/react/components/file-preview';
import '@openviking/react/components/file-preview/style.css';

function Content({ failed, text }: { failed: boolean; text: string }) {
  if (failed) throw new Error('Intentional error-fallback example');
  return <p>{text}</p>;
}

const messages = {
  'zh-CN': {
    fail: '模拟渲染异常',
    custom: '自定义错误展示',
    title: '内容预览',
    other: '其他页签',
    healthy: '此区域仍可使用。',
    error: '宿主自定义的错误展示',
    retry: '重试当前区域',
    hint: '勾选模拟异常，再使用自定义重试恢复；使用默认展示时，先取消模拟异常，再点击重试。',
  },
  'en-US': {
    fail: 'Simulate a rendering failure',
    custom: 'Custom error display',
    title: 'Content preview',
    other: 'Other tab',
    healthy: 'This section is still available.',
    error: 'Host-provided error display',
    retry: 'Retry this section',
    hint: 'Simulate a failure, then use the custom retry action. For the default display, turn off the failure before retrying.',
  },
};

export default function FilePreviewErrorFallback() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const text = messages[locale];
  const [failed, setFailed] = useState(false);
  const [custom, setCustom] = useState(true);
  return (
    <div>
      <p>{text.hint}</p>
      <label>
        <input
          type="checkbox"
          style={{ appearance: 'auto', width: 16, height: 16 }}
          checked={failed}
          onChange={(event) => setFailed(event.target.checked)}
        />{' '}
        {text.fail}
      </label>{' '}
      <label>
        <input
          type="checkbox"
          style={{ appearance: 'auto', width: 16, height: 16 }}
          checked={custom}
          onChange={(event) => setCustom(event.target.checked)}
        />{' '}
        {text.custom}
      </label>
      <div style={{ height: 220, marginTop: 16 }}>
        <FilePreview
          locale={locale}
          slots={{
            errorFallback: custom
              ? (reset) => (
                  <section
                    role="alert"
                    style={{
                      padding: 24,
                      border: '1px dashed currentColor',
                      borderRadius: 8,
                    }}
                  >
                    {text.error}
                    <div>
                      <button
                        type="button"
                        style={{ marginTop: 12, textDecoration: 'underline' }}
                        onClick={() => {
                          setFailed(false);
                          reset();
                        }}
                      >
                        {text.retry}
                      </button>
                    </div>
                  </section>
                )
              : undefined,
          }}
          tabs={[
            {
              key: 'content',
              kind: 'content',
              title: text.title,
              content: <Content failed={failed} text={text.healthy} />,
            },
            {
              key: 'other',
              kind: 'content',
              title: text.other,
              content: <p>{text.healthy}</p>,
            },
          ]}
        />
      </div>
    </div>
  );
}
