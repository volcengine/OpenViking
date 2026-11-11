import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import {
  ScopeCascade,
  type ScopeCascadeSelection,
} from '@openviking/react/modules/scope-cascade';
import '@openviking/react/modules/scope-cascade/style.css';

const api = {
  loadLibraries: () => Promise.resolve([{ id: 'demo', label: 'Demo library' }]),
  loadSpaces: () => Promise.resolve([{ id: 'public', label: 'Public space' }]),
  loadUsers: () =>
    Promise.resolve([
      { id: 'alice', label: 'Alice' },
      { id: 'bob', label: 'Bob' },
    ]),
};
export default function ScopeCascadeSelectionDemo() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [selection, setSelection] = useState<ScopeCascadeSelection | null>(
    null,
  );
  return (
    <div style={{ minHeight: 320 }}>
      <ScopeCascade locale={locale} {...api} onChange={setSelection} />
      <p role="status">
        {selection
          ? `${selection.libraryId} / ${selection.spaceId} / ${selection.userId}`
          : 'Loading scope…'}
      </p>
    </div>
  );
}
