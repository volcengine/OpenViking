import { useCallback, useRef, useState } from 'react';
import { resolveLocale, type Locale } from '../../../shared/locale.js';
import { Modal } from '../../../shared/primitives/modal/modal.js';
import { useViewActive } from '../../../shared/view-activity.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const cover = new URL('./assets/wiki-intro-cover.png', import.meta.url).href;
const poster = new URL('./assets/wiki-cover.png', import.meta.url).href;
const tutorial = new URL('./assets/wiki-intro.mp4', import.meta.url).href;

export function ActivationIntroMedia({ locale }: { locale: Locale }) {
  const labels = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
  const active = useViewActive();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const attachVideo = useCallback((node: HTMLVideoElement | null) => {
    if (videoRef.current && videoRef.current !== node) videoRef.current.pause();
    videoRef.current = node;
  }, []);
  if (!active && open) setOpen(false);

  const close = () => {
    videoRef.current?.pause();
    setOpen(false);
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="ov-console-activation__intro-trigger"
        aria-label={labels.introPlay}
        onClick={() => setOpen(true)}
      >
        <img src={cover} alt="" width={280} height={187} />
      </button>
      {open ? (
        <Modal
          open
          title={labels.introTitle}
          closeLabel={labels.introClose}
          className="ov-console-activation__intro-dialog"
          triggerRef={triggerRef}
          locale={locale}
          onClose={close}
        >
          <h2 className="ov-console-activation__intro-title">
            {labels.introTitle}
          </h2>
          <video
            ref={attachVideo}
            className="ov-console-activation__intro-video"
            src={tutorial}
            poster={poster}
            aria-label={labels.introTitle}
            controls
            autoPlay
            playsInline
            preload="metadata"
          />
        </Modal>
      ) : null}
    </>
  );
}
