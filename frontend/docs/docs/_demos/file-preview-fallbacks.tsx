import { useId, useMemo, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { Checkbox, Button, Select, SelectOption } from '#demo-controls';

import { FilePreview } from '@openviking/react/components/file-preview';
import type { FilePreviewFileTabSlots } from '@openviking/react/components/file-preview';
import '@openviking/react/components/file-preview/style.css';
import '#file-preview-actions.css';

export default function FilePreviewFallbacks() {
  const zh = useLang() === 'zh';
  const id = useId();
  const locale = zh ? 'zh-CN' : 'en-US';
  const text = zh
    ? {
        format: '文件类型',
        image: '图片',
        video: '视频',
        other: '不支持的格式',
        state: '状态',
        ready: '读取来源',
        loading: '加载中',
        missing: '没有来源',
        custom: '自定义状态',
        busy: '宿主加载状态',
        failed: '宿主读取失败状态',
        unsupported: '宿主无来源状态',
        download: '下载原文件',
        retry: '重试',
      }
    : {
        format: 'File type',
        image: 'Image',
        video: 'Video',
        other: 'Unsupported format',
        state: 'State',
        ready: 'Read source',
        loading: 'Loading',
        missing: 'No source',
        custom: 'Custom states',
        busy: 'Host loading state',
        failed: 'Host error state',
        unsupported: 'Host missing-source state',
        download: 'Download original',
        retry: 'Retry',
      };
  const [format, setFormat] = useState('image');
  const [state, setState] = useState('ready');
  const [custom, setCustom] = useState(true);
  // Invalid media deliberately exercises the error state without network requests.
  // The unsupported format retains these bytes for its original-file download.
  const file = useMemo(
    () =>
      state === 'missing'
        ? undefined
        : new Blob(
            [
              new Uint8Array([0, 1, 2, 3, 255]),
              `Example original file (${format})`,
            ],
            { type: 'application/octet-stream' },
          ),
    [state, format],
  );
  const slots: FilePreviewFileTabSlots | undefined = custom
    ? {
        loadingFallback: <p role="status">{text.busy}</p>,
        errorFallback: <p role="status">{text.failed}</p>,
        ...(state === 'missing'
          ? { unsupportedFallback: <p role="status">{text.unsupported}</p> }
          : {}),
        downloadFallback: ({ onDownload, onRetry }) => (
          <div>
            <Button size="small" onClick={onDownload}>
              {text.download}
            </Button>
            {onRetry ? (
              <Button size="small" onClick={onRetry}>
                {text.retry}
              </Button>
            ) : null}
          </div>
        ),
      }
    : undefined;
  return (
    <div lang={locale}>
      <div className="ov-demo-preview-actions__controls">
        <label htmlFor={`${id}-format`}>
          {text.format}{' '}
          <Select
            id={`${id}-format`}
            value={[format]}
            onChange={([value]) => {
              if (value !== undefined) setFormat(value);
            }}
          >
            <SelectOption value="image">{text.image}</SelectOption>
            <SelectOption value="video">{text.video}</SelectOption>
            <SelectOption value="unsupported">{text.other}</SelectOption>
          </Select>
        </label>{' '}
        <label htmlFor={`${id}-state`}>
          {text.state}{' '}
          <Select
            id={`${id}-state`}
            value={[state]}
            onChange={([value]) => {
              if (value !== undefined) setState(value);
            }}
          >
            <SelectOption value="ready">{text.ready}</SelectOption>
            <SelectOption value="loading">{text.loading}</SelectOption>
            <SelectOption value="missing">{text.missing}</SelectOption>
          </Select>
        </label>{' '}
        <Checkbox
          id={`${id}-custom`}
          checked={custom}
          onChange={(next) => setCustom(next)}
        >
          {' '}
          {text.custom}
        </Checkbox>
      </div>
      <div style={{ height: 320 }}>
        <FilePreview
          locale={locale}
          tabs={[
            {
              kind: 'file',
              key: 'fallback',
              loading: state === 'loading',
              slots,
              source: {
                fileName:
                  format === 'image'
                    ? 'sample.png'
                    : format === 'video'
                      ? 'sample.mp4'
                      : 'sample.zip',
                file,
              },
            },
          ]}
        />
      </div>
    </div>
  );
}
