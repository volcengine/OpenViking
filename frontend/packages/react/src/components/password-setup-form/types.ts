import type { ReactNode } from 'react';
import type { AuthFormOptions } from '../../shared/auth/types.js';

export interface PasswordSetupFormProps extends AuthFormOptions {
  account?: ReactNode;
  passwordHint?: ReactNode;
  validatePassword?: (password: string) => string | undefined;
  onSubmit?: (values: { password: string }) => void;
}
