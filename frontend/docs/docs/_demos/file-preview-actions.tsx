import { OvClientError } from '@openviking/react/client';
import { Checkbox, Select, SelectOption } from '#demo-controls';

import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import {
  FilePreview,
  type FilePreviewAction,
  type FilePreviewActionItems,
} from '@openviking/react/components/file-preview';
import '@openviking/react/components/file-preview/style.css';
import '#file-preview-actions.css';

import {
  messages,
  copyIcon,
  historyIcon,
  deleteIcon,
} from '#file-preview-actions-data';

export default function FilePreviewActionsDemo() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const text = messages[locale];
  const [mode, setMode] = useState(4);
  const [fail, setFail] = useState(false);
  const [customToolbar, setCustomToolbar] = useState(false);
  const [notice, setNotice] = useState('');
  const [content, setContent] = useState(
    '# Guide\n\nOpenViking preview actions.',
  );
  const [showModes, setShowModes] = useState(true);
  const [showPath, setShowPath] = useState(true);
  const [showEdit, setShowEdit] = useState(true);
  const command = (
    key: string,
    label: string,
    className?: string,
  ): FilePreviewAction => ({
    key,
    label,
    icon:
      key === 'remove'
        ? deleteIcon
        : key === 'history'
          ? historyIcon
          : copyIcon,
    className,
    onClick: async () => {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      if (fail)
        throw new OvClientError({
          code: 'DEMO_ERROR',
          message: text.failure,
          requestId: 'demo-preview-request',
        });
      setNotice(`${text.done}: ${label}`);
      return { key, simulated: true }; // Non-void results are valid and ignored.
    },
  });
  const copy = command('copy', text.copy);
  const history = command('history', text.history);
  const remove = command(
    'remove',
    text.remove,
    'ov-demo-preview-actions__remove',
  );
  const disabled = { ...command('disabled', text.disabled), disabled: true };
  const layouts: FilePreviewActionItems[] = [
    [],
    [copy],
    [copy, history],
    [[copy, history], [remove]],
    [copy, [], history, [disabled], [], [remove]],
  ];
  return (
    <div className="ov-demo-preview-actions">
      <div className="ov-demo-preview-actions__controls">
        <Checkbox checked={showModes} onChange={(next) => setShowModes(next)}>
          {' '}
          {text.showModes}
        </Checkbox>
        <Checkbox checked={showEdit} onChange={(next) => setShowEdit(next)}>
          {' '}
          {text.showEdit}
        </Checkbox>
        <Checkbox checked={showPath} onChange={(next) => setShowPath(next)}>
          {' '}
          {text.showPath}
        </Checkbox>
        <label>
          {text.mode}{' '}
          <Select
            aria-label={text.mode}
            value={[mode]}
            onChange={([value]) => {
              if (value === undefined) return;
              setMode(Number(value));
              setNotice('');
            }}
          >
            {text.modes.map((label, index) => (
              <SelectOption key={label} value={index}>
                {label}
              </SelectOption>
            ))}
          </Select>
        </label>
        <Checkbox checked={fail} onChange={(next) => setFail(next)}>
          {' '}
          {text.fail}
        </Checkbox>
        <Checkbox
          checked={customToolbar}
          onChange={(next) => setCustomToolbar(next)}
        >
          {' '}
          {text.customToolbar}
        </Checkbox>
      </div>
      <div className="ov-demo-surface" style={{ height: 300 }}>
        <FilePreview
          locale={locale}
          toolbar={{
            copyPath: showPath,
            viewSwitch: showModes,
            edit: showEdit,
          }}
          tabs={[
            {
              kind: 'file',
              key: 'guide',
              toolbar: { viewSwitch: true, edit: true },
              source: {
                content,
                fileName: 'guide.md',
                path: 'viking://resources/guide.md',
              },
              editing: {
                save: (value) => {
                  setContent(value);
                  return Promise.resolve();
                },
              },
              actions: layouts[mode],
              slots: {
                actions: customToolbar ? (
                  <span>{text.customToolbar}</span>
                ) : undefined,
              },
            },
            {
              kind: 'file',
              key: 'second',
              source: {
                fileName: 'second.md',
                path: 'viking://resources/second.md',
                content: '# Second file',
              },
              actions: [history],
            },
          ]}
        />
      </div>
      <p className="ov-demo-hint" role="status">
        {notice || text.hint}
      </p>
    </div>
  );
}
