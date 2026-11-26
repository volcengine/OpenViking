import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { createCommandConsoleDemoRecord } from '#command-console-demo-result';
import { CommandConsole } from '@openviking/react/components/command-console';
import type { CommandConsoleRecord } from '@openviking/react/components/command-console';
import '@openviking/react/components/command-console/style.css';
import {
  createCommandConsoleCommands,
  createCommandConsoleCommandGroups,
  createCommandConsoleQuickCommands,
} from '#command-console-commands';

export default function CommandConsoleEmpty() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [records, setRecords] = useState<readonly CommandConsoleRecord[]>([]);
  const [value, setValue] = useState('');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 720 }}>
      <CommandConsole
        records={records}
        value={value}
        locale={locale}
        commands={createCommandConsoleCommands(locale)}
        commandGroups={createCommandConsoleCommandGroups(locale)}
        quickCommands={createCommandConsoleQuickCommands(locale)}
        onValueChange={setValue}
        onSubmit={(command) => {
          const createdAtMs = Date.now();
          setRecords((current) => [
            ...current,
            {
              ...createCommandConsoleDemoRecord(command, locale),
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
