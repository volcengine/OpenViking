import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { useConsole } from '@openviking/react/modules/console';
import { Tui } from '@openviking/react/components/tui';
import '@openviking/react/components/tui/style.css';
import { consoleTransport } from '#console-transport';

const transport = consoleTransport();

function CommandView() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const state = useConsole({ defaultMode: 'command', locale });
  return (
    <div>
      <button type="button" onClick={state.clear}>
        {locale === 'zh-CN' ? '清空记录' : 'Clear records'}
      </button>
      <div style={{ height: 420 }}>
        <Tui
          value={state.command}
          records={state.records}
          commands={state.commands}
          locale={locale}
          disabled={state.isExecuting}
          onValueChange={state.setCommand}
          onSubmit={(command) => void state.execute(command)}
        />
      </div>
    </div>
  );
}

export default function ConsoleHook() {
  return (
    <OpenVikingProvider fetch={transport}>
      <CommandView />
    </OpenVikingProvider>
  );
}
