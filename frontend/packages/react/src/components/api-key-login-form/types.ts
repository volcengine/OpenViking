import type { AuthFormOptions, AuthAction } from '../../shared/auth/types.js';

export interface ApiKeyLoginFormProps extends AuthFormOptions {
  alternateLogin?: AuthAction;
  onSubmit?: (values: { apiKey: string }) => void;
}
