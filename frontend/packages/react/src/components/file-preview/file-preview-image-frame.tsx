import { useState } from 'react';
import type { ReactNode } from 'react';

/** Observe the renderer's native image load; no extra file request or decoding. */
export function FilePreviewImageFrame({
  children,
  elementRef,
}: {
  children: ReactNode;
  elementRef?: import('react').Ref<HTMLDivElement>;
}) {
  const [dimensions, setDimensions] = useState('');
  return (
    <div
      ref={elementRef}
      className="ov-file-preview__image-frame"
      onLoadCapture={(event) => {
        const image = event.target;
        if (
          image instanceof HTMLImageElement &&
          image.closest('[data-file-viewer-image], [data-ov-preview-image]')
        ) {
          setDimensions(`${image.naturalWidth} × ${image.naturalHeight}`);
        }
      }}
    >
      {children}
      {dimensions ? (
        <span className="ov-file-preview__image-size">{dimensions}</span>
      ) : null}
    </div>
  );
}
