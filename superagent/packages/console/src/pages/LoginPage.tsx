import { useState } from 'react'
import { KeyRound, ShieldCheck } from 'lucide-react'
import { ApiError, api, setStoredApiKey } from '../api'

// The Shared Web asks for the key the agent was set up with (its own login on
// the activity timeline). The console reads the same key from the same place,
// so signing in here also signs in there.
export function LoginPage({ rejected }: { rejected: boolean }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(rejected ? 'That key was refused. Try the one the agent was set up with.' : '')
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const value = key.trim()
    if (!value || busy) return
    setBusy(true)
    setError('')
    try {
      await api.probeKey(value)
      setStoredApiKey(value)
      window.location.reload()
      return
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 401
        ? 'That key was refused. Try the one the agent was set up with.'
        : 'Could not reach the local console service. Check that the agent is running.')
    }
    setBusy(false)
  }

  return <div className="login-shell">
    <form className="login-card" onSubmit={submit}>
      <div className="login-mark"><ShieldCheck size={22} /></div>
      <h1>OpenViking SuperAgent</h1>
      <p>This console is read-only and runs on this machine. Enter the API key the agent was set up with.</p>
      <label className="login-field">
        <KeyRound size={16} />
        <input type="password" value={key} onChange={(event) => setKey(event.target.value)} placeholder="API key"
          aria-label="API key" autoFocus autoComplete="current-password" aria-invalid={Boolean(error)} />
      </label>
      {error && <p className="login-error" role="alert">{error}</p>}
      <button className="button primary" type="submit" disabled={busy || !key.trim()}>{busy ? 'Checking…' : 'Open console'}</button>
      <small>The key stays in this browser. “Lock” in the top bar removes it.</small>
    </form>
  </div>
}
