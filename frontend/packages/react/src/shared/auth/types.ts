import type { Locale } from '../locale.js';
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
  locale?: Locale;
  labels?: Partial<AuthLabels>;
}
export interface AuthMessages extends AuthLabels {
  acceptInvitation: string;
}

/** Store a validation issue independently of its localized display text. */
export type AuthIssue =
  { key: 'required' | 'invalidEmail' | 'mismatch' } | { message: string };
