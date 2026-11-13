import { useEffect, useId, useRef, useState } from 'react';
import { Button, Select, SelectOption } from '#demo-controls';
import { PERMISSIONS, searchPermissions } from './select-remote-data';
import '@openviking/react/style.css';

const COPY = {
  'zh-CN': {
    label: '远程搜索权限',
    placeholder: '请选择权限',
    search: '搜索名称或 reader、editor',
    hint: '模拟远程请求：打开面板加载选项，输入关键词检索（250ms 防抖 + 600ms 延迟）。可输入 reader、editor；error 模拟失败，无匹配词显示空态。',
    failed: '搜索失败，请重试。',
    retry: '重试',
    searching: '正在搜索权限…',
  },
  'en-US': {
    label: 'Remote permission search',
    placeholder: 'Choose permissions',
    search: 'Search a name, reader or editor',
    hint: 'Simulated requests: opening loads options; typing searches them (250ms debounce + 600ms latency). Try reader or editor; error simulates failure, unmatched terms show an empty state.',
    failed: 'Search failed. Please retry.',
    retry: 'Retry',
    searching: 'Searching permissions…',
  },
};

export default function SelectRemoteDemo({
  locale = 'zh-CN',
  disabled = false,
}: {
  locale?: 'zh-CN' | 'en-US';
  disabled?: boolean;
}) {
  const text = COPY[locale];
  const hintId = useId();
  const [value, setValue] = useState<string[]>([]);
  const [results, setResults] = useState(PERMISSIONS);
  const [query, setQuery] = useState('');
  const [phase, setPhase] = useState<'idle' | 'loading' | 'searching'>('idle');
  const [error, setError] = useState(false);
  const request = useRef<AbortController | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // The host owns the mocked network request and debounce timer. User events
  // start them; unmount has no event handler, so this Effect cancels both then.
  useEffect(
    () => () => {
      clearTimeout(debounce.current);
      request.current?.abort();
    },
    [],
  );

  const search = (
    keyword: string,
    nextPhase: 'loading' | 'searching' = 'searching',
  ) => {
    clearTimeout(debounce.current);
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setQuery(keyword);
    setPhase(nextPhase);
    setError(false);
    const load = async () => {
      try {
        const next = await searchPermissions(keyword, controller.signal);
        if (!controller.signal.aborted) setResults(next);
      } catch {
        if (!controller.signal.aborted) {
          setResults([]);
          setError(true);
        }
      } finally {
        if (!controller.signal.aborted) setPhase('idle');
      }
    };
    debounce.current = setTimeout(
      () => {
        void load();
      },
      nextPhase === 'searching' ? 250 : 0,
    );
  };

  return (
    <>
      <p id={hintId}>{text.hint}</p>
      <Select
        aria-label={text.label}
        aria-describedby={hintId}
        locale={locale}
        disabled={disabled}
        multiple
        searchable
        filterOption={false}
        loading={phase === 'loading'}
        searching={phase === 'searching'}
        searchingContent={<strong>{text.searching}</strong>}
        value={value}
        placeholder={text.placeholder}
        searchPlaceholder={text.search}
        onChange={setValue}
        onSearch={search}
        onOpenChange={(open) => {
          if (open) search('', 'loading');
          else {
            clearTimeout(debounce.current);
            request.current?.abort();
            setPhase('idle');
          }
        }}
        emptyContent={error ? text.failed : undefined}
        popupFooter={
          error ? (
            <Button onClick={() => search(query)}>{text.retry}</Button>
          ) : undefined
        }
      >
        {results.map((item) => (
          <SelectOption key={item.value} value={item.value}>
            {locale === 'zh-CN' ? item.zh : item.en}
          </SelectOption>
        ))}
      </Select>
      <output>{JSON.stringify(value)}</output>
    </>
  );
}
