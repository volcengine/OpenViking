import { useMemo, useRef, useState } from 'react';
import { Button } from '#demo-controls';
import { offlineFetch } from '#file-browser-basic-data';
import type {
  FileBrowserPreviewActions,
  FileBrowserPreviewSlots,
} from '@openviking/react/modules/file-browser';

export function usePreviewControls(locale: 'zh-CN' | 'en-US') {
  const text =
    locale === 'zh-CN'
      ? {
          actions: '预览操作区',
          default: '内置操作',
          append: '追加宿主操作',
          custom: '自定义操作区',
          hidden: '隐藏操作区',
          limited: '关闭复制/修改/删除',
          copy: '复制内容',
          copied: '已复制',
          copyFailed: '复制失败，请重试',
          extra: '宿主操作',
          remove: '删除文档',
          history: '查看历史版本',
          importPack: '导入 .ovpack',
          exportPack: '导出 .ovpack',
          deleted: '已模拟删除',
          simulate: '模拟删除失败',
          demo: '离线模拟：仅修改本页数据，刷新页面可恢复；历史和 ovpack 仅演示宿主回调。',
        }
      : {
          actions: 'Preview actions',
          default: 'Built-in actions',
          append: 'Append host actions',
          custom: 'Custom action area',
          hidden: 'Hide action area',
          limited: 'Disable copy/edit/delete',
          copy: 'Copy content',
          copied: 'Copied',
          copyFailed: 'Copy failed; try again',
          extra: 'Host action',
          remove: 'Delete document',
          history: 'Version history',
          importPack: 'Import .ovpack',
          exportPack: 'Export .ovpack',
          deleted: 'Simulated deletion',
          simulate: 'Simulate deletion failure',
          demo: 'Offline simulation: only page data changes; reload to restore. History and ovpack demonstrate host callbacks.',
        };
  const [mode, setMode] = useState('default');
  const [fail, setFail] = useState(false);
  const failRef = useRef(false);
  failRef.current = fail;
  const [hint, setHint] = useState<{
    kind:
      | 'copied'
      | 'copyFailed'
      | 'extra'
      | 'history'
      | 'importPack'
      | 'exportPack'
      | 'deleted';
    uri?: string;
  }>();
  const fetch = useMemo(
    () => (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE' && failRef.current)
        return Promise.resolve(
          new Response(
            JSON.stringify({
              status: 'error',
              error: {
                code: 'DEMO_DELETE_FAILED',
                message: 'Demo deletion unavailable',
              },
            }),
            { status: 503, headers: { 'content-type': 'application/json' } },
          ),
        );
      return offlineFetch(input, init);
    },
    [],
  );
  const previewActions: FileBrowserPreviewActions = {
    ...(mode === 'limited'
      ? { copyContent: false, edit: false, delete: false }
      : {}),
    onHistory: (uri) => setHint({ kind: 'history', uri }),
    onImportPack: (uri) => setHint({ kind: 'importPack', uri }),
    onExportPack: (uri) => setHint({ kind: 'exportPack', uri }),
    onDelete: (uri) => setHint({ kind: 'deleted', uri }),
  };
  const previewSlots: FileBrowserPreviewSlots = {
    actions:
      mode === 'hidden'
        ? null
        : mode === 'custom'
          ? (actions) => (
              <>
                {actions.canCopyContent ? (
                  <Button
                    disabled={actions.busy}
                    onClick={() =>
                      void actions
                        .copyContent()
                        .then((ok) =>
                          setHint({ kind: ok ? 'copied' : 'copyFailed' }),
                        )
                    }
                  >
                    {text.copy}
                  </Button>
                ) : null}
                {actions.canDelete ? (
                  <Button disabled={actions.busy} onClick={actions.deleteFile}>
                    {text.remove}
                  </Button>
                ) : null}
              </>
            )
          : undefined,
    extraActions:
      mode === 'append' ? (
        <Button onClick={() => setHint({ kind: 'extra' })}>{text.extra}</Button>
      ) : undefined,
    menuItems:
      mode === 'append'
        ? [
            {
              key: 'host',
              label: text.extra,
              separatorBefore: true,
              onSelect: () => setHint({ kind: 'extra' }),
            },
          ]
        : undefined,
  };
  return {
    fetch,
    previewActions,
    previewSlots,
    controls: (
      <>
        <label>
          {text.actions}{' '}
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value)}
          >
            {(
              ['default', 'append', 'custom', 'hidden', 'limited'] as const
            ).map((value) => (
              <option key={value} value={value}>
                {text[value]}
              </option>
            ))}
          </select>
        </label>{' '}
        <label>
          <input
            type="checkbox"
            style={{
              display: 'inline-block',
              appearance: 'auto',
              width: 16,
              height: 16,
            }}
            checked={fail}
            onChange={(event) => setFail(event.target.checked)}
          />{' '}
          {text.simulate}
        </label>
        <p>{text.demo}</p>
        {hint ? (
          <p role="status">
            {text[hint.kind]} {hint.uri}
          </p>
        ) : null}
      </>
    ),
  };
}
