import { useLang } from '@rspress/core/runtime';
import {
  ScopeCascade,
  useScopeCascade,
} from '@openviking/react/modules/scope-cascade';
import '@openviking/react/modules/scope-cascade/style.css';
import { listLibraries, listSpaces, listUsers } from '#scope-switcher-requests';

const api = {
  loadLibraries: (signal: AbortSignal) => listLibraries('', signal),
  loadSpaces: listSpaces,
  loadUsers: (libraryId: string, spaceId: string, signal: AbortSignal) =>
    listUsers(`${libraryId}/${spaceId}`, signal),
};

export default function ScopeSwitcherAsync() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const controller = useScopeCascade(api);
  return (
    <div style={{ minHeight: 380 }}>
      <ScopeCascade locale={componentLocale} controller={controller} />
    </div>
  );
}
