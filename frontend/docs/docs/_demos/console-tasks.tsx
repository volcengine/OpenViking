import { useMemo } from 'react';
import { Button } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import '@openviking/react/modules/console/style.css';
import { createTaskDemo } from '#console-tasks-service';

export default function ConsoleTasks() {
  const demo = useMemo(createTaskDemo, []);
  const zh = useLang() === 'zh';
  return (
    <>
      <p>
        {zh
          ? '模拟任务，不访问后端；状态最多等待 3 秒刷新。'
          : 'Simulated tasks; no backend calls. Status refreshes within 3 seconds.'}
      </p>
      <div
        style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}
      >
        <Button onClick={() => demo.finish(false)}>
          {zh ? '模拟完成' : 'Simulate completion'}
        </Button>
        <Button onClick={() => demo.finish(true)}>
          {zh ? '模拟失败' : 'Simulate failure'}
        </Button>
      </div>
      <div style={{ height: 640 }}>
        <OpenVikingProvider baseURL="https://demo.invalid" fetch={demo.fetch}>
          <Console
            locale={zh ? 'zh-CN' : 'en-US'}
            defaultMode="command"
            quickCommands={[
              {
                name: 'add-resource',
                value: 'ov add-resource https://example.com/demo',
              },
            ]}
            taskService={demo.taskService}
            chatOptions={{ service: demo.service }}
          />
        </OpenVikingProvider>
      </div>
    </>
  );
}
