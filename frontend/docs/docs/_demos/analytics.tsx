import { Button, Checkbox } from '#demo-controls';
import { useMemo, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import type {
  OpenVikingAnalyticsEvent,
  OpenVikingAnalyticsOptions,
} from '@openviking/react/client';
import { FileTree } from '@openviking/react/components/file-tree';
import { FilePreview } from '@openviking/react/components/file-preview';
import '@openviking/react/components/file-tree/style.css';
import '@openviking/react/components/file-preview/style.css';

export default function AnalyticsDemo() {
  const zh = useLang() === 'zh';
  const locale = zh ? 'zh-CN' : 'en-US';
  const [enabled, setEnabled] = useState(false);
  const [onlyCopy, setOnlyCopy] = useState(false);
  const [fail, setFail] = useState(false);
  const [selected, setSelected] = useState('');
  const [events, setEvents] = useState<OpenVikingAnalyticsEvent[]>([]);
  const analytics = useMemo<OpenVikingAnalyticsOptions | undefined>(
    () =>
      enabled
        ? {
            events: onlyCopy
              ? ['openviking_file_preview_copy_path_click']
              : undefined,
            onEvent(event) {
              setEvents((current) => [...current, event].slice(-10));
              if (fail)
                return Promise.reject(new Error('Simulated receiver failure'));
            },
          }
        : undefined,
    [enabled, onlyCopy, fail],
  );
  return (
    <div>
      <p>
        {zh
          ? '本例仅在页面显示事件，不向任何平台发送。点击文件或复制路径，观察下方日志。'
          : 'Events stay on this page. Select a file or copy its path to inspect the log.'}
      </p>
      <div className="ov-demo-controls">
        <Checkbox checked={enabled} onChange={setEnabled}>
          {zh ? '开启埋点' : 'Enable analytics'}
        </Checkbox>
        <Checkbox checked={onlyCopy} onChange={setOnlyCopy}>
          {zh ? '只接收复制路径点击' : 'Only copy-path clicks'}
        </Checkbox>
        <Checkbox checked={fail} onChange={setFail}>
          {zh ? '模拟上报失败' : 'Simulate receiver failure'}
        </Checkbox>
        <Button size="small" onClick={() => setEvents([])}>
          {zh ? '清空日志' : 'Clear log'}
        </Button>
      </div>
      <OpenVikingProvider locale={locale} analytics={analytics}>
        <FileTree
          rows={[
            {
              kind: 'entry',
              id: 'example',
              depth: 0,
              name: 'example.txt',
              uri: 'viking://resources/example.txt',
              type: 'file',
            },
          ]}
          selectedId={selected}
          onSelect={(row) => setSelected(row.id)}
        />
        <div style={{ height: 280 }}>
          <FilePreview
            tabs={[
              {
                kind: 'file',
                key: 'example',
                source: {
                  fileName: 'example.txt',
                  path: 'viking://resources/example.txt',
                  content: 'Hello OpenViking',
                },
              },
            ]}
          />
        </div>
      </OpenVikingProvider>
      <p>
        {zh
          ? '清空日志不重置曝光周期。日志最多显示最近十条事件。'
          : 'Clearing the log does not reset exposure deduplication. The last ten events are shown.'}
      </p>
      <pre
        aria-live="polite"
        style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
      >
        {events.length
          ? events
              .map(({ name, params }) => `${name} ${JSON.stringify(params)}`)
              .join('\n')
          : zh
            ? '暂无事件'
            : 'No events'}
      </pre>
    </div>
  );
}
