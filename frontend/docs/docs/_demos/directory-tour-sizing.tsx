import { useCallback, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { useLang } from '@rspress/core/runtime';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import '@openviking/react/components/directory-tour/style.css';
import '#directory-tour-basic.css';

const limits: Record<string, CSSProperties> = {
  percentage: { maxHeight: '50%' },
  inline: { maxWidth: 200, maxHeight: 130 },
  'max-content': { maxWidth: 'max-content', maxHeight: 'max-content' },
  'min-content': { maxWidth: 'min-content', maxHeight: 'min-content' },
  'fit-content': { maxWidth: 'fit-content', maxHeight: 'fit-content' },
  inherit: { maxWidth: 'inherit', maxHeight: 'inherit' },
};

export default function DirectoryTourSizing() {
  const zh = useLang() === 'zh';
  const anchor = useRef<HTMLButtonElement>(null);
  const [preset, setPreset] = useState('percentage');
  const [open, setOpen] = useState(false);
  const locateTarget = useCallback(() => Promise.resolve(anchor.current), []);
  const text = zh
    ? '长内容应在卡片内换行和滚动。'
    : 'Long content wraps and scrolls inside the card. ';
  return (
    <div className="ov-directory-tour-sizing">
      <select
        aria-label={zh ? '尺寸约束' : 'Size constraints'}
        value={preset}
        onChange={(event) => setPreset(event.target.value)}
      >
        {[
          'percentage',
          'class',
          ...Object.keys(limits).filter((name) => name !== 'percentage'),
        ].map((name) => (
          <option key={name}>{name}</option>
        ))}
      </select>
      <button ref={anchor} type="button" onClick={() => setOpen(true)}>
        {zh ? '测试尺寸约束' : 'Test sizing'}
      </button>
      <DirectoryTour
        open={open}
        locale={zh ? 'zh-CN' : 'en-US'}
        steps={[
          {
            id: 'sizing',
            target: 'sizing',
            title: zh ? '尺寸约束' : 'Size constraints',
            description: text.repeat(80),
          },
        ]}
        locateTarget={locateTarget}
        className={
          preset === 'class' ? 'ov-directory-tour-sizing__custom' : undefined
        }
        style={limits[preset]}
        onClose={() => setOpen(false)}
        onComplete={() => setOpen(false)}
      />
    </div>
  );
}
