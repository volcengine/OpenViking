import { useLang } from '@rspress/core/runtime';
import { useRef, useState } from 'react';
import { Button, Checkbox, Input } from '#demo-controls';
import {
  ContextPicker,
  type ContextPickerEntry,
} from '@openviking/react/components/context-picker';
import { useContextPickerDemo } from '#context-picker-demo';
import '@openviking/react/components/context-picker/style.css';

type Mode = 'target' | 'sources' | 'skill';
const initial = (mode: Mode): ContextPickerEntry[] => [
  {
    uri:
      mode === 'skill'
        ? 'viking://system/compile/example-skills/llm-wiki/SKILL.md'
        : 'viking://resources/wiki/archive',
    name: mode === 'skill' ? 'SKILL.md' : 'archive',
    type: mode === 'skill' ? 'file' : 'directory',
  },
];

function Example({ mode, isZh }: { mode: Mode; isZh: boolean }) {
  const data = useContextPickerDemo(mode, isZh);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(initial(mode));
  const [saved, setSaved] = useState(initial(mode));
  const [disabled, setDisabled] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [fail, setFail] = useState(false);
  const [error, setError] = useState('');
  const [name, setName] = useState('new-folder');
  const title =
    mode === 'sources'
      ? isZh
        ? '选择生成 Wiki 的源目录'
        : 'Select source directories'
      : mode === 'skill'
        ? isZh
          ? '选择编译 Wiki 的 Skill'
          : 'Select a compilation skill'
        : isZh
          ? '选择输出目录'
          : 'Select an output directory';
  const confirm = async () => {
    if (confirming) return;
    setConfirming(true);
    setError('');
    try {
      await new Promise((resolve) => setTimeout(resolve, 400));
      if (fail)
        throw new Error(
          isZh
            ? '模拟保存失败，请重试。'
            : 'Simulated save failure. Please retry.',
        );
      setSaved(value);
      setOpen(false);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setConfirming(false);
    }
  };
  const footerStart =
    mode === 'skill' ? (
      <Button
        type="outline"
        disabled={disabled || confirming}
        onClick={() => {
          const parent = data.add(
            'viking://system/compile/example-skills',
            'uploaded-skill',
          );
          const uri = data.add(parent, 'SKILL.md');
          setValue([{ uri, name: 'SKILL.md', type: 'file' }]);
        }}
      >
        {isZh ? '添加 Skill（模拟）' : 'Add skill (simulated)'}
      </Button>
    ) : mode === 'target' ? (
      <Button
        type="outline"
        disabled={disabled || confirming || !value.length || !name.trim()}
        onClick={() => {
          const parent = value[0];
          if (!parent) return;
          const uri = data.add(parent.uri, name.trim());
          setValue([{ uri, name: name.trim(), type: 'directory' }]);
        }}
      >
        {isZh ? '新建子目录（模拟）' : 'New subdirectory (simulated)'}
      </Button>
    ) : undefined;
  return (
    <div>
      <div className="ov-demo-controls">
        <Button
          ref={trigger}
          disabled={disabled}
          onClick={() => {
            setValue(saved);
            setError('');
            data.onQueryChange('');
            setOpen(true);
          }}
        >
          {title}
        </Button>
        <Checkbox checked={disabled} onChange={setDisabled}>
          {isZh ? '禁用选择' : 'Disable selection'}
        </Checkbox>
        <Checkbox checked={fail} onChange={setFail}>
          {isZh ? '模拟保存失败' : 'Simulate save failure'}
        </Checkbox>
      </div>
      <p>
        {isZh
          ? '离线模拟；输入 error 测试搜索失败，输入不存在的名称查看空态。'
          : 'Offline simulation. Search for error to fail, or an unknown name for an empty state.'}
      </p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {saved.map((entry) => entry.uri).join('\n')}
      </pre>
      <ContextPicker
        {...data}
        open={open}
        triggerRef={trigger}
        title={title}
        value={value}
        onChange={setValue}
        multiple={mode === 'sources'}
        locale={isZh ? 'zh-CN' : 'en-US'}
        labels={{
          search:
            mode === 'skill'
              ? isZh
                ? 'Skill 名称'
                : 'Skill name'
              : isZh
                ? '目录名称'
                : 'Directory name',
        }}
        disabled={disabled}
        confirming={confirming}
        error={
          error ||
          (data.error ? (
            <>
              {data.error}
              <Button type="text" onClick={data.retry}>
                {isZh ? '重试' : 'Retry'}
              </Button>
            </>
          ) : undefined)
        }
        onConfirm={() => void confirm()}
        onCancel={() => setOpen(false)}
        slots={{
          footerStart,
          content:
            mode === 'target' ? (
              <Input
                aria-label={isZh ? '子目录名称' : 'Subdirectory name'}
                value={name}
                disabled={disabled || confirming}
                onChange={(event) => setName(event.target.value)}
              />
            ) : undefined,
        }}
      />
    </div>
  );
}

export default function ContextPickerBasic() {
  const isZh = useLang() === 'zh';
  const [mode, setMode] = useState<Mode>('sources');
  return (
    <div>
      <div className="ov-demo-controls">
        {(['sources', 'target', 'skill'] as const).map((item) => (
          <Button
            key={item}
            type={mode === item ? 'primary' : 'outline'}
            onClick={() => setMode(item)}
          >
            {item === 'sources'
              ? isZh
                ? '源目录多选'
                : 'Multiple directories'
              : item === 'target'
                ? isZh
                  ? '输出目录单选'
                  : 'Single directory'
                : isZh
                  ? 'Skill 单选'
                  : 'Single skill'}
          </Button>
        ))}
      </div>
      <Example key={mode} mode={mode} isZh={isZh} />
    </div>
  );
}
