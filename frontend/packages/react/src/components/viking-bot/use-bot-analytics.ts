import { useTrack } from '../../shared/analytics/use-track.js';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import type { VikingBotProps } from './types.js';

export function useBotAnalytics(onSend: VikingBotProps['onSend']) {
  const track = useTrack();
  const exposure = useExposure('console_chat_panel_show');
  return {
    exposure,
    send: onSend
      ? (draft: Parameters<NonNullable<VikingBotProps['onSend']>>[0]) => {
          track('viking_bot_send_click');
          return onSend(draft);
        }
      : undefined,
  };
}
