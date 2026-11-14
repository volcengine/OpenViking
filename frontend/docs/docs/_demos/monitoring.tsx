import { Button, Checkbox } from '#demo-controls';
import { useMemo, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import type {
  OpenVikingMonitoringEvent,
  OpenVikingMonitoringOptions,
} from '@openviking/react/client';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';
import '@openviking/react/modules/scope-cascade/style.css';

const loadSpaces = () =>
  Promise.resolve([{ id: 'demo-space', label: 'Demo space' }]);
const loadUsers = () =>
  Promise.resolve([{ id: 'demo-user', label: 'Demo user' }]);

export default function MonitoringDemo() {
  const zh = useLang() === 'zh';
  const [enabled, setEnabled] = useState(false);
  const [fail, setFail] = useState(false);
  const [receiverFails, setReceiverFails] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [events, setEvents] = useState<OpenVikingMonitoringEvent[]>([]);
  const monitoring = useMemo<OpenVikingMonitoringOptions | undefined>(
    () =>
      enabled
        ? {
            onEvent(event) {
              setEvents((current) => [...current, event].slice(-10));
              if (receiverFails)
                return Promise.reject(new Error('Simulated receiver failure'));
            },
          }
        : undefined,
    [enabled, receiverFails],
  );
  const loadLibraries = useMemo(
    () => async () => {
      void generation;
      await new Promise((resolve) => setTimeout(resolve, 250));
      if (fail) throw new Error('Simulated library loading failure');
      return [{ id: 'demo-library', label: 'Demo library' }];
    },
    [fail, generation],
  );
  return (
    <div>
      <p>
        {zh
          ? '使用模拟数据，事件只显示在本页。开启监控后重新加载；切换接收器不会重新请求。'
          : 'Simulated data; events stay on this page. Enable monitoring, then reload. Changing the receiver does not restart requests.'}
      </p>
      <div className="ov-demo-controls">
        <Checkbox checked={enabled} onChange={setEnabled}>
          {zh ? '开启监控' : 'Enable monitoring'}
        </Checkbox>
        <Checkbox checked={fail} onChange={setFail}>
          {zh ? '模拟加载失败' : 'Simulate loading failure'}
        </Checkbox>
        <Checkbox checked={receiverFails} onChange={setReceiverFails}>
          {zh ? '模拟接收器失败' : 'Simulate receiver failure'}
        </Checkbox>
        <Button
          size="small"
          onClick={() => setGeneration((value) => value + 1)}
        >
          {zh ? '重新加载' : 'Reload'}
        </Button>
        <Button size="small" onClick={() => setEvents([])}>
          {zh ? '清空日志' : 'Clear log'}
        </Button>
      </div>
      <OpenVikingProvider
        locale={zh ? 'zh-CN' : 'en-US'}
        monitoring={monitoring}
      >
        <ScopeCascade
          loadLibraries={loadLibraries}
          loadSpaces={loadSpaces}
          loadUsers={loadUsers}
        />
      </OpenVikingProvider>
      <pre
        aria-live="polite"
        style={{
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
          maxHeight: 420,
          overflow: 'auto',
        }}
      >
        {events.length
          ? events.map((event) => JSON.stringify(event, null, 2)).join('\n')
          : zh
            ? '暂无事件'
            : 'No events'}
      </pre>
    </div>
  );
}
