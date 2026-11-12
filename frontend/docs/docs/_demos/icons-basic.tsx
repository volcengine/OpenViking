import { useState } from 'react';
import { Button } from '#demo-controls';
import {
  ArrowRightIcon,
  AudioIcon,
  LibraryIcon,
  PdfIcon,
  PptIcon,
  SpaceIcon,
  UserIcon,
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
const scopeIcons = [
  { name: 'LibraryIcon', Icon: () => <LibraryIcon name="research" /> },
  { name: 'SpaceIcon', Icon: SpaceIcon },
  { name: 'UserIcon', Icon: UserIcon },
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
          <Button
            key={value}
            size="small"
            type={size === value ? 'primary' : 'default'}
            aria-pressed={size === value}
            onClick={() => setSize(value)}
          >
            {value}px
          </Button>
        ))}
      </div>
      <div className="ov-demo-row">
        {scopeIcons.map(({ name, Icon }) => (
          <span key={name} className="ov-demo-row">
            <Icon />
            <code>{name}</code>
          </span>
        ))}
      </div>
      <p className="ov-demo-row">
        <PdfIcon width={24} height={24} aria-label="PDF" />
        <span>Standalone icon with aria-label=&quot;PDF&quot;</span>
      </p>
    </div>
  );
}
