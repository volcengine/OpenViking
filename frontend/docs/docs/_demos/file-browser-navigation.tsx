import { Button, Checkbox } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { useMemo, useRef, useState } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import type { FileBrowserHandle } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

const ROOT = 'viking://resources';
const DIRECTORY = `${ROOT}/guide`;
const BRANCH = `${DIRECTORY}/examples`;
const FILE = `${BRANCH}/README`;
const entries = [
  ...Array.from({ length: 200 }, (_, index) => ({
    uri: `${DIRECTORY}/note-${index}.md`,
  })),
  { uri: BRANCH, isDir: true },
];

export default function FileBrowserNavigation() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const text =
    locale === 'zh-CN'
      ? {
          directory: '定位并刷新目录',
          branch: '定位跨页目录',
          root: '定位根目录',
          file: '定位无扩展名文件',
          fail: '模拟第二页加载失败',
          ready: '离线模拟：guide 有 201 个条目，examples 位于第二页。',
          success: '定位完成',
          missing: '目标未定位',
          failed: '模拟目录加载失败',
        }
      : {
          directory: 'Reveal and refresh directory',
          branch: 'Reveal later-page directory',
          root: 'Reveal root',
          file: 'Reveal extensionless file',
          fail: 'Simulate second-page failure',
          ready:
            'Offline simulation: guide has 201 entries; examples is on page two.',
          success: 'Revealed',
          missing: 'Target not revealed',
          failed: 'Simulated listing failure',
        };
  const handle = useRef<FileBrowserHandle>(null);
  const [fail, setFail] = useState(false);
  const failure = useRef(false);
  failure.current = fail;
  const [result, setResult] = useState<boolean | string>();
  const fetch = useMemo(
    () => (input: RequestInfo | URL) => {
      const url = new URL(
        input instanceof Request ? input.url : String(input),
        'https://demo.test',
      );
      const uri = url.searchParams.get('uri');
      const offset = Number(url.searchParams.get('offset'));
      let value: unknown = '';
      let hasMore = false;
      if (url.pathname.endsWith('/fs/ls')) {
        if (uri === DIRECTORY && offset && failure.current)
          return Promise.resolve(
            Response.json(
              {
                status: 'error',
                error: {
                  code: 'DEMO_LISTING_FAILED',
                  message: 'Simulated listing failure',
                },
              },
              { status: 503 },
            ),
          );
        value =
          uri === ROOT
            ? [{ uri: DIRECTORY, isDir: true }]
            : uri === DIRECTORY
              ? entries.slice(offset, offset + 200)
              : uri === BRANCH
                ? [{ uri: FILE }]
                : [];
        hasMore = uri === DIRECTORY && offset + 200 < entries.length;
      } else if (url.pathname.endsWith('/fs/stat')) {
        value = { uri, isDir: uri !== FILE };
      }
      return Promise.resolve(
        Response.json({ status: 'ok', result: value, has_more: hasMore }),
      );
    },
    [],
  );
  const open = async (uri: string) => {
    try {
      setResult(await handle.current?.openResource(uri));
    } catch (error) {
      setResult(error instanceof Error ? error.message : String(error));
    }
  };
  return (
    <div>
      <OpenVikingProvider fetch={fetch}>
        <FileBrowser
          ref={handle}
          rootUri={ROOT}
          locale={locale}
          dashboardKinds={[]}
          style={{ height: 480 }}
        />
      </OpenVikingProvider>
      <div className="ov-demo-controls">
        <Button onClick={() => void open(DIRECTORY)}>{text.directory}</Button>
        <Button onClick={() => void open(BRANCH)}>{text.branch}</Button>
        <Button onClick={() => void open(ROOT)}>{text.root}</Button>
        <Button onClick={() => void open(FILE)}>{text.file}</Button>
        <Checkbox checked={fail} onChange={setFail}>
          {text.fail}
        </Checkbox>
        <span role="status">
          {result === undefined
            ? text.ready
            : result === true
              ? text.success
              : result === false
                ? text.missing
                : text.failed}
        </span>
      </div>
    </div>
  );
}
