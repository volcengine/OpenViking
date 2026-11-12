import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { createTuiDemoRecord } from '#tui-demo-result';
import { Tui } from '@openviking/react/components/tui';
import type { TuiRecord } from '@openviking/react/components/tui';
import '@openviking/react/components/tui/style.css';
import {
  createTuiCommands,
  createTuiCommandGroups,
  createTuiQuickCommands,
} from '#tui-commands';

export default function TuiEmpty() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [records, setRecords] = useState<readonly TuiRecord[]>([]);
  const [value, setValue] = useState('');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 720 }}>
      <Tui
        records={records}
        value={value}
        locale={locale}
        commands={createTuiCommands(locale)}
        commandGroups={createTuiCommandGroups(locale)}
        quickCommands={createTuiQuickCommands(locale)}
        onValueChange={setValue}
        onSubmit={(command) => {
          const createdAtMs = Date.now();
          setRecords((current) => [
            ...current,
            {
              ...createTuiDemoRecord(command, locale),
              id: `empty-demo-${current.length}`,
              createdAtMs,
            },
          ]);
          setValue('');
        }}
      />
    </div>
  );
}
