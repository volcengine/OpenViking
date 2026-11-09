import type { ReactNode } from 'react';

export interface AuthAction {
  label: string;
  onClick?: () => void;
}
export interface AuthAgreements {
  items: readonly { label: string; href: string }[];
  confirmation?: {
    title?: string;
    description?: string;
    confirmText?: string;
    cancelText?: string;
  };
}
export interface AuthLabels {
  email: string;
  password: string;
  apiKey: string;
  newPassword: string;
  confirmation: string;
  showPassword: string;
  hidePassword: string;
  required: string;
  invalidEmail: string;
  mismatch: string;
  login: string;
  send: string;
  resend: string;
  confirm: string;
  agreementPrefix: string;
  agreementTitle: string;
  agreementDescription: string;
  agree: string;
  cancel: string;
  account: string;
}
export interface AuthFormOptions {
  loading?: boolean;
  error?: ReactNode;
  disabled?: boolean;
  agreements?: AuthAgreements;
  back?: AuthAction;
  className?: string;
  title?: ReactNode;
  description?: ReactNode;
  labels?: Partial<AuthLabels>;
}
export const defaultLabels: AuthLabels = {
  email: 'Email',
  password: 'Password',
  apiKey: 'API Key',
  newPassword: 'New password',
  confirmation: 'Confirm new password',
  showPassword: 'Show password',
  hidePassword: 'Hide password',
  required: 'This field is required.',
  invalidEmail: 'Enter a valid email address.',
  mismatch: 'Passwords do not match.',
  login: 'Log in',
  send: 'Send reset email',
  resend: 'Resend email',
  confirm: 'Confirm and log in',
  agreementPrefix: 'I have read and agree to',
  agreementTitle: 'Read and accept the agreements',
  agreementDescription:
    'Please read and accept the following agreements before continuing.',
  agree: 'Agree and continue',
  cancel: 'Cancel',
  account: 'Login account',
};
