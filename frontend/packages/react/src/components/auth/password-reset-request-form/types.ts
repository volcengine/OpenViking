import type { ReactNode } from 'react';
import type { AuthFormOptions } from '../../../shared/auth/types.js';

export interface PasswordResetRequestFormProps extends AuthFormOptions {
  defaultEmail?: string;
  sentMessage?: { email: string; message: ReactNode };
  retryAfter?: number;
  onSubmit?: (values: { email: string }) => void;
}
