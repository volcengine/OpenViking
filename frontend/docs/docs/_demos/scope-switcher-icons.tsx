import mask from './assets/scope-switcher/library-0.svg';
import clip from './assets/scope-switcher/library-1.svg';
import base from './assets/scope-switcher/library-2.svg';
import glow from './assets/scope-switcher/library-3.svg';
import pink from './assets/scope-switcher/library-4.svg';
import soft from './assets/scope-switcher/library-5.svg';
import light from './assets/scope-switcher/library-6.svg';
import shine from './assets/scope-switcher/library-7.svg';
import space from './assets/scope-switcher/space.svg';
import user from './assets/scope-switcher/user.svg';

/** Original layered Figma icon; host-owned imagery stays outside the component. */
export function LibraryIcon() {
  const layers = [
    { src: base, left: 0.67, top: 0.67 },
    { src: glow, left: 0.92 - 2.057, top: 0.92 - 2.056 },
    { src: pink, left: 0, top: 0 },
    { src: soft, left: 0.26 - 2.056, top: 0.26 - 2.056 },
    { src: light, left: 0.03 - 2.232, top: 0.44 - 2.232 },
    { src: shine, left: 4.34 - 3.676, top: 5.03 - 3.676 },
  ];
  return (
    <span
      style={{
        display: 'block',
        position: 'relative',
        width: 14,
        height: 14,
        maskImage: `url("${mask}"), url("${clip}")`,
        maskSize: '14px 14px',
        maskRepeat: 'no-repeat',
      }}
    >
      <span style={{ position: 'absolute', left: -0.67, top: -0.67 }}>
        {layers.map(({ src, left, top }, index) => (
          <img
            key={src}
            src={src}
            alt=""
            style={{
              position: 'absolute',
              maxWidth: 'none',
              left,
              top,
              ...(index === 5 ? { transform: 'rotate(-0.17deg)' } : {}),
            }}
          />
        ))}
      </span>
    </span>
  );
}
export function SpaceIcon() {
  return <img src={space} alt="" width={14} height={14} />;
}
export function UserIcon() {
  return <img src={user} alt="" width={14} height={14} />;
}
