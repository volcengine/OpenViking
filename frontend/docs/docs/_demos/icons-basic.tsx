import { useState } from 'react';
import {
  ArrowRightIcon,
  AudioIcon,
  PdfIcon,
  PptIcon,
  VideoIcon,
  WordIcon,
} from '@openviking/react/icons';

const icons = [
  { name: 'ArrowRightIcon', Icon: ArrowRightIcon },
  { name: 'AudioIcon', Icon: AudioIcon },
  { name: 'PdfIcon', Icon: PdfIcon },
  { name: 'PptIcon', Icon: PptIcon },
  { name: 'VideoIcon', Icon: VideoIcon },
  { name: 'WordIcon', Icon: WordIcon },
];
const sizes = [16, 24, 32];

export default function IconsExample() {
  const [size, setSize] = useState(16);

  return (
    <div>
      <div className="ov-demo-row">
        {icons.map(({ name, Icon }) => (
          <span key={name} className="ov-demo-row">
            <Icon width={size} height={size} />
            <code>{name}</code>
          </span>
        ))}
      </div>
      <div className="ov-demo-controls" role="group" aria-label="Icon size">
        {sizes.map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={size === value}
            onClick={() => setSize(value)}
          >
            {value}px
          </button>
        ))}
      </div>
      <p className="ov-demo-row">
        <PdfIcon width={24} height={24} aria-label="PDF" />
        <span>Standalone icon with aria-label=&quot;PDF&quot;</span>
      </p>
    </div>
  );
}
