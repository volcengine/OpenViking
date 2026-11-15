import { useMemo, useRef, useState } from 'react';
import { Checkbox, Button, Select, SelectOption } from '#demo-controls';

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
          demo: '离线模拟：仅修改本页数据，刷新页面可恢复；历史、配置及 ovpack 使用原生接口形状的模拟数据。',
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
          demo: 'Offline simulation: only page data changes; reload to restore. History, configuration and ovpack use simulated native API responses.',
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
            {
              status: 503,
              headers: {
                'content-type': 'application/json',
                'x-request-id': 'demo-delete-request',
              },
            },
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
  };
  const previewSlots: FileBrowserPreviewSlots = {
    actions:
      mode === 'hidden'
        ? null
        : mode === 'custom'
          ? (actions) => (
              <>
                {actions.defaultActions
                  .flatMap((item) => ('key' in item ? [item] : item))
                  .filter((item) => item.key === 'copyContent')
                  .map((item) => (
                    <Button
                      key={item.key}
                      disabled={actions.busy}
                      onClick={() =>
                        void Promise.resolve(item.onClick())
                          .then(() => setHint({ kind: 'copied' }))
                          .catch(() => setHint({ kind: 'copyFailed' }))
                      }
                    >
                      {text.copy}
                    </Button>
                  ))}
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
    actionItems:
      mode === 'append'
        ? (context) => [
            ...context.defaultActions,
            [
              {
                key: 'host',
                icon: <span aria-hidden="true">↗</span>,
                label: text.extra,
                onClick: () => setHint({ kind: 'extra' }),
              },
            ],
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
          <Select
            value={[mode]}
            onChange={([value]) => {
              if (value !== undefined) setMode(value);
            }}
          >
            {(
              ['default', 'append', 'custom', 'hidden', 'limited'] as const
            ).map((value) => (
              <SelectOption key={value} value={value}>
                {text[value]}
              </SelectOption>
            ))}
          </Select>
        </label>{' '}
        <Checkbox checked={fail} onChange={(next) => setFail(next)}>
          {' '}
          {text.simulate}
        </Checkbox>
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
