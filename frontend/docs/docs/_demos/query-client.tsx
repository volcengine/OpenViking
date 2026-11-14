import { Button } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { QueryClient } from '@tanstack/react-query';
import { useMemo, useRef, useState } from 'react';
import { OpenVikingProvider, OV_PATHS } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

export default function QueryClientDemo() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const zh = locale === 'zh-CN';
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, staleTime: 5000 } },
      }),
  );
  const [calls, setCalls] = useState(0);
  const [failure, setFailure] = useState(false);
  const server = useRef({ failure: false });
  const fetch = useMemo<typeof globalThis.fetch>(
    () => async (input) => {
      const url = new URL(
        input instanceof Request ? input.url : String(input),
        'https://demo.example',
      );
      if (url.pathname === OV_PATHS.fsLs) {
        setCalls((count) => count + 1);
        await new Promise((resolve) => setTimeout(resolve, 100));
        if (server.current.failure)
          return new Response('Simulated directory failure', { status: 503 });
        const root = url.searchParams.get('uri');
        return Response.json({
          status: 'ok',
          result:
            root === 'viking://resources'
              ? [
                  { uri: `${root}/handbook`, isDir: true },
                  { uri: `${root}/examples`, isDir: true },
                ]
              : [],
        });
      }
      return Response.json({ status: 'ok', result: '' });
    },
    [],
  );
  return (
    <div>
      <p>
        {zh
          ? '模拟服务：两个文件浏览实例共享同一份目录缓存。'
          : 'Simulated service: two browsers share the same directory cache.'}
      </p>
      <div className="ov-demo-controls">
        <Button onClick={() => void queryClient.invalidateQueries()}>
          {zh ? '刷新目录' : 'Refresh directories'}
        </Button>
        <Button
          onClick={() => {
            server.current.failure = !failure;
            setFailure(!failure);
          }}
        >
          {failure
            ? zh
              ? '恢复模拟服务'
              : 'Restore simulated service'
            : zh
              ? '模拟请求失败'
              : 'Simulate request failure'}
        </Button>
        <span role="status">
          {zh
            ? `目录请求：${calls}；失败模拟：${failure ? '开启' : '关闭'}`
            : `Directory requests: ${calls}; failure simulation: ${failure ? 'on' : 'off'}`}
        </span>
      </div>
      <OpenVikingProvider
        queryClient={queryClient}
        fetch={fetch}
        locale={locale}
        requestConcurrency={{ [OV_PATHS.fsLs]: 3 }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns:
              'repeat(auto-fit, minmax(min(100%, 300px), 1fr))',
            gap: 16,
            gridAutoRows: 360,
          }}
        >
          <FileBrowser rootUri="viking://resources" />
          <FileBrowser rootUri="viking://resources" />
        </div>
      </OpenVikingProvider>
    </div>
  );
}
