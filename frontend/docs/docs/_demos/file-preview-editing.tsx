import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { FilePreview } from '@openviking/react/components/file-preview';
import { codeFiles } from '#file-preview-code-data';
import '@openviking/react/components/file-preview/style.css';
import '#file-preview-actions.css';

const messages = {
  'zh-CN': {
    file: '代码文件',
    fail: '模拟保存失败',
    hint: '点击编辑，检查高亮、行号和撤销；勾选模拟保存失败，检查草稿保留与重试。保存到示例内存。',
    failure: '模拟保存失败',
    saved: '已保存到示例内存',
  },
  'en-US': {
    file: 'Code file',
    fail: 'Simulate save failure',
    hint: 'Click Edit to try highlighting, line numbers and undo. Simulate a failed save to check draft retention and retry. Saves stay in demo memory.',
    failure: 'Simulated save failure',
    saved: 'Saved to demo memory',
  },
};

export default function FilePreviewEditing() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const text = messages[locale];
  const [contents, setContents] = useState<Record<string, string>>({});
  const [fail, setFail] = useState(false);
  const [saved, setSaved] = useState(false);
  const [fileName, setFileName] = useState(codeFiles[0]!.fileName);
  const file = codeFiles.find((item) => item.fileName === fileName)!;
  return (
    <>
      <div className="ov-demo-preview-actions__controls">
        <label>
          {text.file}
          <select
            value={fileName}
            onChange={(event) => {
              setFileName(event.target.value);
              setSaved(false);
            }}
          >
            {codeFiles.map((item) => (
              <option key={item.fileName} value={item.fileName}>
                {item.fileName}
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
      <p>{text.hint}</p>
      <div style={{ height: 360 }}>
        <FilePreview
          key={file.fileName}
          locale={locale}
          tabs={[
            {
              key: file.fileName,
              kind: 'file',
              source: {
                ...file,
                content: contents[file.fileName] ?? file.content,
              },
              editing: {
                save: async (content) => {
                  await new Promise((resolve) => setTimeout(resolve, 400));
                  if (fail) throw new Error(text.failure);
                  setContents((current) => ({
                    ...current,
                    [file.fileName]: content,
                  }));
                  setSaved(true);
                },
              },
            },
          ]}
        />
      </div>
      {saved ? <p role="status">{text.saved}</p> : null}
    </>
  );
}
