import type { AuthFormOptions, AuthAction } from '../../shared/auth/types.js';

export interface EmailLoginFormProps extends AuthFormOptions {
  defaultEmail?: string;
  readOnlyEmail?: boolean;
  forgotPassword?: AuthAction;
  alternateLogin?: AuthAction;
  onSubmit?: (values: { email: string; password: string }) => void;
}
