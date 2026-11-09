import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { Tui } from '@openviking/react/components/tui';
import '@openviking/react/components/tui/style.css';
import { createTuiRecords } from '#tui-records';
import { createTuiDemoRecord } from '#tui-demo-result';
import { createTuiCommands, createTuiCommandGroups } from '#tui-commands';

function TuiDemo({ locale }: { locale: 'en' | 'zh' }) {
  const isChinese = locale === 'zh';
  const [records, setRecords] = useState(() => createTuiRecords(isChinese));
  const [value, setValue] = useState('');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 1000 }}>
      <Tui
        records={records}
        value={value}
        locale={locale}
        commands={createTuiCommands(locale)}
        commandGroups={createTuiCommandGroups(locale)}
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
                      ...createTuiDemoRecord(command, locale),
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

export default function TuiBasic() {
  const locale = useLang() === 'zh' ? 'zh' : 'en';
  return <TuiDemo key={locale} locale={locale} />;
}
