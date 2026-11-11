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
      await new Promise((resolve) => setTimeout(resolve, 600));
      if (fail) throw new Error(text.failure);
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
        <label>
          <input
            type="checkbox"
            checked={showModes}
            onChange={(event) => setShowModes(event.target.checked)}
          />{' '}
          {text.showModes}
        </label>
        <label>
          <input
            type="checkbox"
            checked={showEdit}
            onChange={(event) => setShowEdit(event.target.checked)}
          />{' '}
          {text.showEdit}
        </label>
        <label>
          <input
            type="checkbox"
            checked={showPath}
            onChange={(event) => setShowPath(event.target.checked)}
          />{' '}
          {text.showPath}
        </label>
        <label>
          {text.mode}{' '}
          <select
            aria-label={text.mode}
            value={mode}
            onChange={(event) => {
              setMode(Number(event.target.value));
              setNotice('');
            }}
          >
            {text.modes.map((label, index) => (
              <option key={label} value={index}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={fail}
            onChange={(event) => setFail(event.target.checked)}
          />{' '}
          {text.fail}
        </label>
      </div>
      <div className="ov-demo-surface" style={{ height: 300 }}>
        <FilePreview
          key={mode}
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
