import type { AuthMessages } from '../types.js';

const messages = {
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
  acceptInvitation: 'Accept invitation',
} satisfies AuthMessages;

export default messages;
