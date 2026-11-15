import { ScrollText } from 'lucide-react'
import { api } from '../api'
import { Card, CardHeader, EmptyState, ErrorState, Loading, StatusBadge } from '../components'
import { dateTime } from '../format'
import { useApi } from '../hooks'

export function AuditPage({ instance }: { instance: string }) {
  const audit = useApi(() => api.audit(instance), `audit:${instance}`)
  return <div className="page-stack"><div className="page-heading"><div><div className="eyebrow">SYSTEM / AUDIT</div><h1>Audit</h1><p>Mutation actor, action, revision, redacted diff, and outcome.</p></div></div>
    <Card><CardHeader title="Control-plane activity" description="Credentials, cookies, authorization headers, and full backups are excluded" action={<ScrollText size={18} className="muted-icon" />} />
      {audit.loading ? <Loading /> : audit.error ? <ErrorState error={audit.error} retry={audit.reload} /> : audit.data?.items.length ? <div className="audit-list">{audit.data.items.map((entry) => <div className="audit-row" key={entry.id}><div><strong>{entry.action}</strong><span>{entry.id} · {entry.actor} · {entry.instanceId}</span></div><code>{entry.revision ?? '—'}</code><time>{dateTime(entry.createdAt)}</time><StatusBadge status={entry.result} /></div>)}</div> : <EmptyState title="No mutations yet" description="Control actions will appear here with redacted diffs." />}
    </Card>
  </div>
}
