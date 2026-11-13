import { Button, Checkbox } from '#demo-controls';
import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import {
  EmailLoginForm,
  ApiKeyLoginForm,
  PasswordSetupForm,
  PasswordResetRequestForm,
  LoginMethodSelector,
  InvitationConfirmation,
} from '@openviking/react';
import '@openviking/react/components/auth/email-login-form/style.css';
import '@openviking/react/components/auth/api-key-login-form/style.css';
import '@openviking/react/components/auth/password-setup-form/style.css';
import '@openviking/react/components/auth/password-reset-request-form/style.css';
import '@openviking/react/components/auth/login-method-selector/style.css';
import '@openviking/react/components/auth/invitation-confirmation/style.css';

const agreements = {
  items: [
    { label: 'Example terms', href: 'https://example.com/terms' },
    { label: 'Example privacy policy', href: 'https://example.com/privacy' },
  ],
  confirmation: {
    title: 'Demo agreements',
    description: 'These are demonstration links. No account is created.',
  },
};
export default function AuthFormsDemo() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [view, setView] = useState('email');
  const [withAgreements, setWithAgreements] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  const [sent, setSent] = useState<{ email: string; message: string }>();
  const switchView = (id: string) => {
    setView(id);
    setError('');
    setResult('');
  };
  const finish = () => {
    setLoading(false);
    setResult('Demo completed. No request was sent.');
  };
  const common = {
    locale,
    loading,
    error,
    agreements: withAgreements ? agreements : undefined,
    back: { label: 'Back to methods', onClick: () => switchView('methods') },
  } as const;
  const start = () => {
    setError('');
    setLoading(true);
    setResult(
      'Simulated pending operation. Use the completion/failure buttons below.',
    );
  };
  return (
    <div className="ov-auth">
      <p>
        Interactive demo only. No network requests, accounts, credentials or
        storage are created.
      </p>
      <nav className="ov-auth__actions" aria-label="Authentication demos">
        {['methods', 'email', 'api-key', 'forgot', 'setup', 'invitation'].map(
          (id) => (
            <Button
              key={id}
              htmlType="button"
              disabled={loading}
              onClick={() => switchView(id)}
            >
              {id}
            </Button>
          ),
        )}
      </nav>
      <p>
        <Checkbox
          checked={withAgreements}
          onChange={(next) => setWithAgreements(next)}
        >
          {' '}
          Configure agreements
        </Checkbox>
      </p>
      <div key={`${view}-${withAgreements}`}>
        {view === 'methods' && (
          <LoginMethodSelector
            title="Log in"
            methods={[
              { id: 'email', label: 'Email login' },
              { id: 'api-key', label: 'API Key login' },
            ]}
            onSelect={switchView}
          />
        )}
        {view === 'email' && (
          <EmailLoginForm
            {...common}
            title="Email login"
            forgotPassword={{
              label: 'Forgot password?',
              onClick: () => switchView('forgot'),
            }}
            alternateLogin={{
              label: 'Use API Key',
              onClick: () => switchView('api-key'),
            }}
            onSubmit={start}
          />
        )}
        {view === 'api-key' && (
          <ApiKeyLoginForm
            {...common}
            title="API Key login"
            alternateLogin={{
              label: 'Use email',
              onClick: () => switchView('email'),
            }}
            onSubmit={start}
          />
        )}
        {view === 'forgot' && (
          <PasswordResetRequestForm
            {...common}
            title="Request a password reset"
            sentMessage={sent}
            onSubmit={({ email }) =>
              setSent({
                email,
                message:
                  'Demo reset email accepted. Open Setup to simulate following the mail link.',
              })
            }
          />
        )}
        {view === 'setup' && (
          <PasswordSetupForm
            {...common}
            title="Set a new password"
            account="demo@example.com"
            passwordHint="Demo rule: at least 8 characters, including a letter, a digit and a symbol."
            validatePassword={(value) =>
              value.length >= 8 &&
              /[a-z]/i.test(value) &&
              /[0-9]/.test(value) &&
              /[^a-z0-9]/i.test(value)
                ? undefined
                : 'Password does not meet the demo rule.'
            }
            onSubmit={start}
          />
        )}
        {view === 'invitation' && (
          <InvitationConfirmation
            locale={locale}
            title="Join a space"
            loading={loading}
            error={error}
            details={[
              { label: 'Collection', value: 'Example library' },
              { label: 'Space', value: 'Design' },
              { label: 'User', value: 'demo-user' },
            ]}
            onAccept={start}
            decline={{
              label: 'Decline',
              onClick: () => setResult('Decline intent sent to host.'),
            }}
            defer={{
              label: 'Later',
              onClick: () => setResult('Defer intent sent to host.'),
            }}
          />
        )}
      </div>
      {result && <p role="status">{result}</p>}
      {loading && (
        <div>
          <Button htmlType="button" onClick={finish}>
            Complete simulation
          </Button>{' '}
          <Button
            htmlType="button"
            onClick={() => {
              setLoading(false);
              setError('Simulated service failure. Please try again.');
            }}
          >
            Fail simulation
          </Button>
        </div>
      )}
    </div>
  );
}
