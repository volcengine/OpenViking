import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, FlaskConical, X } from 'lucide-react'
import { ApiError, isDemoMode } from '../api'

export function DemoNotice() {
  if (!isDemoMode()) return null
  return <div className="notice info demo-notice"><FlaskConical size={17} /><div><strong>Interactive mock demo</strong><span>All data and mutations are isolated in the mock server. Nothing here controls a real OVSA instance.</span></div></div>
}

export function ActionFeedback({ error, success }: { error: Error | null; success: string | null }) {
  if (error) return <div className={`notice ${error instanceof ApiError && error.uncertain ? 'warning' : 'danger'}`} role="alert"><AlertTriangle size={17} /><div><strong>{error instanceof ApiError ? error.code.replaceAll('_', ' ') : 'Action failed'}</strong><span>{error.message}{error instanceof ApiError && error.operationId ? ` Operation: ${error.operationId}` : ''}</span></div></div>
  if (success) return <div className="notice info" role="status"><CheckCircle2 size={17} /><div><strong>Mock state updated</strong><span>{success}</span></div></div>
  return null
}

export function ConfirmDialog({ title, children, confirmLabel, danger = false, onConfirm, onClose, busy = false }: {
  title: string
  children: ReactNode
  confirmLabel: string
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
  busy?: boolean
}) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) onClose() }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [busy, onClose])
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose() }}>
    <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
      <div className="dialog-heading"><h2 id="dialog-title">{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose} disabled={busy}><X size={18} /></button></div>
      <div className="dialog-body">{children}</div>
      <div className="dialog-actions"><button className="button secondary" onClick={onClose} disabled={busy}>Cancel</button><button className={`button ${danger ? 'danger-button' : 'primary'}`} onClick={onConfirm} disabled={busy}>{busy ? 'Working…' : confirmLabel}</button></div>
    </section>
  </div>
}

export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const run = async (action: () => Promise<unknown>, message: string) => {
    setBusy(true); setError(null); setSuccess(null)
    try { await action(); setSuccess(message); return true }
    catch (reason) { setError(reason instanceof Error ? reason : new Error(String(reason))); return false }
    finally { setBusy(false) }
  }
  return { busy, error, success, run, clear: () => { setError(null); setSuccess(null) } }
}
