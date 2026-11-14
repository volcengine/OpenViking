import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import {
  CommandConsole,
  type CommandConsoleRecord,
} from '@openviking/react/components/command-console';
import '@openviking/react/components/command-console/style.css';
import { Fault, RenderFailureDemo } from '#render-failure';

export default function CommandConsoleCrash() {
  const zh = useLang() === 'zh';
  const [value, setValue] = useState('');
  const records: readonly CommandConsoleRecord[] = [
    {
      id: 'broken',
      command: 'ov write viking://resources/acme/notes.md',
      status: 'success',
      content: {
        variant: 'write-result',
        lines: [
          <Fault key="line">
            {zh ? '已写入 1 个文件。' : 'Wrote 1 file.'}
          </Fault>,
        ],
      },
    },
    {
      id: 'healthy',
      command: 'ov health',
      status: 'success',
      content: {
        variant: 'write-result',
        lines: [zh ? '服务运行正常。' : 'The service is healthy.'],
      },
    },
  ];
  return (
    <RenderFailureDemo
      hint={
        zh
          ? '宿主渲染的第一条结果出错时，只有这条结果显示兜底，其他记录和输入区照常可用。'
          : 'When the first host-rendered result fails, only that result shows the fallback; the other record and the input stay usable.'
      }
    >
      <div style={{ height: 420 }}>
        <CommandConsole
          locale={zh ? 'zh-CN' : 'en-US'}
          records={records}
          value={value}
          onValueChange={setValue}
        />
      </div>
    </RenderFailureDemo>
  );
}
