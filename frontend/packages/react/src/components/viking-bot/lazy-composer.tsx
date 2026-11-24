import { useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { forwardRef } from 'react';
import { LazyContent } from '../../shared/lazy-content.js';

import type { VikingBotComposerProps, VikingBotComposerRef } from './types.js';

/** 重型视图按需加载，减少控制台首屏下载与解析开销。 */
const load = async () => {
  const { VikingBotComposerView } = await import('./viking-bot-composer.js');
  return { default: VikingBotComposerView };
};

/** 输入区。The composer. */
export const VikingBotComposer = forwardRef<
  VikingBotComposerRef,
  VikingBotComposerProps
>(function VikingBotComposer({ slots, ...props }, ref) {
  return (
    <ErrorBoundary
      monitoringRegion="composer"
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).composer}
      locale={props.locale}
    >
      <LazyContent
        monitoringTarget="viking_bot_composer_load"
        load={load}
        contentProps={{ ...props, ref }}
        fallback={
          <div className="ov-viking-bot__composer ov-viking-bot__composer--loading">
            {props.disabled && props.disabledReason ? (
              <p className="ov-viking-bot__composer-hint" role="status">
                {props.disabledReason}
              </p>
            ) : null}
          </div>
        }
      />
    </ErrorBoundary>
  );
});
