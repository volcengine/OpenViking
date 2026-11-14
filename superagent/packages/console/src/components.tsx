import type { ReactNode } from 'react'
import { AlertCircle, ArrowRight, LoaderCircle } from 'lucide-react'
import { titleCase } from './format'

export function StatusBadge({ status, label }: { status?: string | null; label?: string }) {
  const value = status ?? 'unknown'
  const tone = ['running', 'ready', 'healthy', 'done', 'sent', 'succeeded', 'synced', 'connected'].includes(value.toLowerCase())
    ? 'positive'
    : ['failed', 'unhealthy', 'uncertain', 'error', 'stopped', 'offline'].includes(value.toLowerCase())
      ? 'negative'
      : ['waiting', 'queued', 'degraded', 'pending', 'recovering', 'relayed_to_inbox'].includes(value.toLowerCase())
        ? 'warning' : 'neutral'
  return <span className={`badge ${tone}`}><span className="badge-dot" />{label ?? titleCase(value)}</span>
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>
}

export function CardHeader({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return <div className="card-header"><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{action}</div>
}

export function EmptyState({ icon, title, description, action }: { icon?: ReactNode; title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state">{icon}<h3>{title}</h3><p>{description}</p>{action}</div>
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  return <EmptyState icon={<AlertCircle size={24} />} title="Couldn’t load this view" description={error.message}
    action={retry && <button className="button secondary" onClick={retry}>Try again</button>} />
}

export function Loading() {
  return <div className="loading" role="status"><LoaderCircle className="spin" size={22} /><span>Loading console data…</span></div>
}

export function Definition({ label, children }: { label: string; children: ReactNode }) {
  return <div className="definition"><dt>{label}</dt><dd>{children}</dd></div>
}

export function TextLink({ href, children }: { href: string; children: ReactNode }) {
  return <a className="text-link" href={href}>{children}<ArrowRight size={14} /></a>
}
