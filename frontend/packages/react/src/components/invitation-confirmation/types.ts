import type { ReactNode } from 'react';
import type { Locale } from '../../shared/locale.js';
import type { AuthAction } from '../../shared/auth/types.js';
export interface InvitationConfirmationProps {
  details: readonly { label: string; value: ReactNode }[];
  loading?: boolean;
  disabled?: boolean;
  error?: ReactNode;
  decline?: AuthAction;
  defer?: AuthAction;
  className?: string;
  title?: ReactNode;
  description?: ReactNode;
  locale?: Locale;
  acceptLabel?: string;
  onAccept?: () => void;
}
