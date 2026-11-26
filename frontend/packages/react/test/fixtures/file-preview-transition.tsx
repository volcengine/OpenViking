import { FilePreview } from '@openviking/react/components/file-preview';
import type { ReactElement } from 'react';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';

const level = (key: string, content: string) => ({
  key,
  label: key,
  source: { content, fileName: `${key}.md` },
});

function CancelCase() {
  const [active, setActive] = useState('L0');
  return (
    <FilePreview
      tabs={[
        {
          kind: 'file',
          key: 'guide',
          source: { content: '# A body', fileName: 'a.md' },
          activeLevelKey: active,
          onLevelChange: setActive,
          loading: active === 'L1',
          levels: [level('L0', '# A body'), level('L1', '# B')],
        },
      ]}
    />
  );
}

function SnapshotCase() {
  const [active, setActive] = useState('L0');
  const [bReady, setBReady] = useState(false);
  const [cReady, setCReady] = useState(false);
  const host = window as unknown as {
    __setBReady: () => void;
    __setCReady: () => void;
  };
  host.__setBReady = () => setBReady(true);
  host.__setCReady = () => setCReady(true);
  return (
    <FilePreview
      tabs={[
        {
          kind: 'file',
          key: 'guide',
          source: { content: '# A body', fileName: 'a.md' },
          activeLevelKey: active,
          onLevelChange: setActive,
          loading: (active === 'L1' && !bReady) || (active === 'L2' && !cReady),
          levels: [
            level('L0', '# A body'),
            level('L1', bReady ? '# B body' : '# B placeholder'),
            level('L2', '# C body'),
          ],
        },
      ]}
    />
  );
}

const cases: Record<string, () => ReactElement> = {
  cancel: CancelCase,
  snapshot: SnapshotCase,
};

const Case = cases[new URLSearchParams(location.search).get('case') ?? ''];
createRoot(document.getElementById('root')!).render(Case ? <Case /> : null);
