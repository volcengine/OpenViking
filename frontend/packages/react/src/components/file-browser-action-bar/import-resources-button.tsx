import { forwardRef, useImperativeHandle, useRef } from 'react';
import { useExposureRef } from '../../shared/analytics/use-exposure.js';
import {
  Button,
  type ButtonProps,
} from '../../shared/primitives/button/button.js';

/** Keep collection on the mounted native button even if a host clones its ref. */
export const ImportResourcesButton = forwardRef<HTMLButtonElement, ButtonProps>(
  function ImportResourcesButton(props, ref) {
    const button = useRef<HTMLButtonElement>(null);
    const exposure = useExposureRef(
      'file_browser_import_resources_show',
      button,
    );
    useImperativeHandle(ref, () => button.current!, []);
    return <Button {...props} ref={exposure} />;
  },
);
