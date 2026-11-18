import { useLang } from '@rspress/core/runtime';
import { useRef, useState } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import type { VikingBotReference } from '@openviking/react/components/viking-bot';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import type { FileBrowserHandle } from '@openviking/react/modules/file-browser';
import { Console } from '@openviking/react/modules/console';
import type { ConsoleMode } from '@openviking/react/modules/console';
import '@openviking/react/modules/style.css';
import { consoleTransport } from '#console-transport';

const ROOT = 'viking://resources';
const entries: readonly VikingBotReference[] = [
  { uri: `${ROOT}/handbook`, name: 'handbook', type: 'directory' },
  { uri: `${ROOT}/research`, name: 'research', type: 'directory' },
  { uri: `${ROOT}/README.md`, name: 'README.md', type: 'file' },
];
const rows = entries.map((entry) => ({
  ...entry,
  id: entry.uri,
  kind: 'entry' as const,
  depth: 0,
}));
const consoleFetch = consoleTransport();
const demoFetch: typeof fetch = (input, init) => {
  const url = new URL(input instanceof Request ? input.url : String(input));
  const uri = url.searchParams.get('uri');
  const reply = (result: unknown) =>
    Promise.resolve(Response.json({ status: 'ok', result }));
  if (url.pathname === '/api/v1/fs/ls')
    return reply(
      uri === ROOT
        ? entries.map(({ uri, name, type }) => ({
            uri,
            name,
            isDir: type === 'directory',
          }))
        : [],
    );
  if (url.pathname === '/api/v1/fs/stat')
    return reply({ uri, isDir: uri !== `${ROOT}/README.md` });
  return consoleFetch(input, init);
};

export default function ModulesBasic() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const browser = useRef<FileBrowserHandle>(null);
  const [mode, setMode] = useState<ConsoleMode>('conversation');
  const [directory, setDirectory] = useState<VikingBotReference>();
  const [manual, setManual] = useState<readonly VikingBotReference[]>([]);
  const [draft, setDraft] = useState('');
  const [sent, setSent] = useState<readonly VikingBotReference[]>();
  const automatic =
    directory && !manual.some((item) => item.uri === directory.uri)
      ? directory
      : undefined;
  const selected = automatic ? [automatic, ...manual] : manual;
  return (
    <OpenVikingProvider baseURL="https://demo.invalid" fetch={demoFetch}>
      <div className="ov-demo-modules">
        <FileBrowser
          ref={browser}
          locale={locale}
          rootUri={ROOT}
          readOnly
          dashboardKinds={[]}
          style={{ height: 460 }}
          onSelect={({ uri, name, type }) => {
            if (mode !== 'conversation') return;
            setDirectory(
              type === 'directory' ? { uri, name, type } : undefined,
            );
          }}
        />
        <Console
          locale={locale}
          mode={mode}
          onModeChange={setMode}
          style={{ height: 460 }}
          onOpenUri={(uri) => void browser.current?.openResource(uri)}
          conversation={{
            messages: [],
            draft,
            onDraftChange: setDraft,
            references: {
              rows,
              selected,
              onChange: (next) => {
                if (
                  directory &&
                  !next.some((item) => item.uri === directory.uri)
                )
                  setDirectory(undefined);
                setManual(next.filter((item) => item.uri !== automatic?.uri));
              },
            },
            onSend: ({ references }) => {
              setDraft('');
              setSent(references ?? []);
              setDirectory(undefined);
              setManual([]);
            },
          }}
        />
      </div>
      {sent ? (
        <output style={{ overflowWrap: 'anywhere' }}>
          {locale === 'zh-CN' ? '本次发送的引用：' : 'References sent: '}
          {sent.map((item) => item.uri).join(', ') || '—'}
        </output>
      ) : null}
    </OpenVikingProvider>
  );
}
