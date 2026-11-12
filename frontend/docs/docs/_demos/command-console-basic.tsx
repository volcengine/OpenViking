import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { CommandConsole } from '@openviking/react/components/command-console';
import '@openviking/react/components/command-console/style.css';
import { createCommandConsoleRecords } from '#command-console-records';
import { createCommandConsoleDemoRecord } from '#command-console-demo-result';
import {
  createCommandConsoleCommands,
  createCommandConsoleCommandGroups,
} from '#command-console-commands';

function CommandConsoleDemo({ locale }: { locale: 'en-US' | 'zh-CN' }) {
  const isChinese = locale === 'zh-CN';
  const [records, setRecords] = useState(() =>
    createCommandConsoleRecords(isChinese),
  );
  const [value, setValue] = useState('');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 1000 }}>
      <CommandConsole
        records={records}
        value={value}
        locale={locale}
        commands={createCommandConsoleCommands(locale)}
        commandGroups={createCommandConsoleCommandGroups(locale)}
        onValueChange={setValue}
        onSubmit={(command) => {
          const createdAtMs = Date.now();
          const pendingId = `pending-${records.length}`;
          setRecords((current) => [
            ...current,
            { id: pendingId, command, status: 'pending', createdAtMs },
          ]);
          setValue('');
          window.setTimeout(() => {
            setRecords((current) =>
              current.map((record) =>
                record.id !== pendingId
                  ? record
                  : {
                      ...createCommandConsoleDemoRecord(command, locale),
                      id: pendingId,
                      createdAtMs: record.createdAtMs,
                    },
              ),
            );
          }, 900);
        }}
        onOpenUri={(uri) =>
          window.alert(isChinese ? `打开 ${uri}（演示）` : `Open ${uri} (demo)`)
        }
        onCopy={(text) => console.log('copied', text)}
      />
    </div>
  );
}

export default function CommandConsoleBasic() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  return <CommandConsoleDemo key={locale} locale={locale} />;
}
