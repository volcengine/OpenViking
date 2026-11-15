import { useState } from 'react'
import { Download, FileBox, TerminalSquare } from 'lucide-react'
import { api, isDemoMode } from '../api'
import { Card, CardHeader, EmptyState, ErrorState, Loading, StatusBadge } from '../components'
import { ActionFeedback, DemoNotice, useAction } from '../components/Controls'
import { dateTime } from '../format'
import { useApi } from '../hooks'
import type { Capabilities } from '../types'

export function DiagnosticsPage({ instance, capabilities }: { instance: string; capabilities: Capabilities }) {
  const [stream, setStream] = useState('daemon')
  const summary = useApi(() => api.diagnostics(instance), `diagnostics:${instance}`)
  const logs = useApi(() => api.logs(instance, stream), `logs:${instance}:${stream}`)
  const action = useAction()
  const canBundle = Boolean(capabilities.modules['diagnostics.bundle']?.effective && isDemoMode())
  return <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">OPERATIONS / DIAGNOSTICS</div><h1>Logs & diagnostics</h1><p>Bounded redacted streams and explicit diagnostic bundle contents.</p></div><button className="button primary" disabled={!canBundle || action.busy} onClick={() => action.run(() => api.createDiagnosticBundle(instance), 'Diagnostic bundle Operation completed. The mock artifact contains metadata only.') }><FileBox size={15} /> Generate bundle</button></div>
    <DemoNotice />
    <ActionFeedback error={action.error} success={action.success} />
    {summary.loading ? <Loading /> : summary.error || !summary.data ? <ErrorState error={summary.error ?? new Error('No diagnostics returned')} retry={summary.reload} /> : <>
      <div className="stat-grid diagnostic-stats"><Card className="stat-card"><div><span>Release</span><strong className="small-stat">{summary.data.release}</strong><small>{summary.data.platform}</small></div></Card><Card className="stat-card"><div><span>Database</span><strong className="small-stat">{summary.data.database.state}</strong><small>Local state store</small></div></Card><Card className="stat-card"><div><span>OpenViking</span><strong className="small-stat">{summary.data.openviking.state}</strong><small>{summary.data.openviking.version ?? 'Version unavailable'}</small></div></Card><Card className="stat-card"><div><span>Configuration</span><strong className="small-stat">{summary.data.drift ? 'Drift' : 'Synced'}</strong><small>{summary.data.connectors.ready}/{summary.data.connectors.total} connectors ready</small></div></Card></div>
      <Card><CardHeader title="Redacted log stream" description="Predefined streams only; no paths, tail commands, environment, credentials, or native transcripts" action={<label className="select-filter"><TerminalSquare size={14} /><select aria-label="Log stream" value={stream} onChange={(e) => setStream(e.target.value)}><option>daemon</option><option>web</option></select></label>} />
        {logs.loading ? <Loading /> : logs.error ? <ErrorState error={logs.error} retry={logs.reload} /> : logs.data?.items.length ? <div className="log-list">{logs.data.items.map((entry) => <div className="log-row" key={entry.id}><time>{dateTime(entry.createdAt)}</time><StatusBadge status={entry.severity} /><code>{entry.message}</code></div>)}</div> : <EmptyState title="No log entries" description="This stream has no redacted entries in the bounded window." />}
      </Card>
      <Card><CardHeader title="Diagnostic bundle policy" description="The generated artifact has a size limit and a 24-hour retention period" />
        <div className="bundle-contents"><span>Release & platform</span><span>Configuration drift</span><span>Database / OV health</span><span>Runtime & Connector summary</span><span>Bounded redacted logs</span></div><div className="boundary-note"><Download size={15} /> Excludes full chats, environment variables, secret files, Provider credentials, and native Session data.</div>
      </Card>
    </>}
  </div>
}
