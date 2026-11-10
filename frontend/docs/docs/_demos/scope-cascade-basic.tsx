import { useLang } from '@rspress/core/runtime';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';
import '@openviking/react/modules/scope-cascade/style.css';

// Mock interfaces only: replace these adapters with the three host APIs.
const libraries = [
  { id: 'research', label: 'Research' },
  { id: 'support', label: 'Support' },
];
async function mock<T>(value: T, signal: AbortSignal): Promise<T> {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, 450);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true },
    );
  });
  return value;
}
const api = {
  loadLibraries: (signal: AbortSignal) => mock(libraries, signal),
  loadSpaces: (libraryId: string, signal: AbortSignal) =>
    mock(
      [
        { id: `${libraryId}-public`, label: 'Public space' },
        { id: `${libraryId}-private`, label: 'Private space' },
      ],
      signal,
    ),
  loadUsers: (_libraryId: string, spaceId: string, signal: AbortSignal) =>
    mock(
      [
        { id: `${spaceId}-alice`, label: 'Alice' },
        { id: `${spaceId}-bob`, label: 'Bob' },
      ],
      signal,
    ),
};

export default function ScopeCascadeBasic() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  return (
    <div style={{ minHeight: 320 }}>
      <ScopeCascade locale={componentLocale} {...api} />
    </div>
  );
}
