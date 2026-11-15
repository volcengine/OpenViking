import { useState } from 'react'
import { KeyRound, Plus, RefreshCw } from 'lucide-react'
import { api, isDemoMode } from '../api'
import { Card, CardHeader, EmptyState, ErrorState, Loading, StatusBadge } from '../components'
import { ActionFeedback, ConfirmDialog, DemoNotice, useAction } from '../components/Controls'
import { dateTime, reasonText } from '../format'
import { useApi } from '../hooks'
import type { Capabilities, ModelProvider } from '../types'

export function ProvidersPage({ instance, capabilities }: { instance: string; capabilities: Capabilities }) {
  const providers = useApi(() => api.providers(instance), `providers:${instance}`)
  const canWrite = Boolean(capabilities.modules['providers.write']?.effective && isDemoMode())
  const [credentialFor, setCredentialFor] = useState<ModelProvider | null>(null)
  const [secret, setSecret] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [newProvider, setNewProvider] = useState({ id: 'ark-backup', protocol: 'openai-compatible', baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', models: 'demo/model-small' })
  const [binding, setBinding] = useState({ providerId: 'ark-main', runtime: 'pi', model: 'volcengine/deepseek-v3.2' })
  const action = useAction()
  const refresh = async (message: string, work: () => Promise<unknown>) => {
    const ok = await action.run(work, message)
    if (ok) { setCredentialFor(null); setSecret(''); setAddOpen(false); await providers.reload() }
  }
  return <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">OPERATIONS / MODEL PROVIDERS</div><h1>Model providers</h1><p>Logical model routing, credential references, and Runtime-specific projection health.</p></div><button className="button primary" disabled={!canWrite} onClick={() => setAddOpen(true)}><Plus size={15} /> Add provider</button></div>
    <DemoNotice />
    {!canWrite && <div className="notice neutral"><KeyRound size={17} /><div><strong>Provider management unavailable</strong><span>{isDemoMode() ? reasonText(capabilities.modules['providers.write']?.reasonCode) : 'Read-only outside the isolated mock demo.'}</span></div></div>}
    <ActionFeedback error={action.error} success={action.success} />
    {providers.loading ? <Loading /> : providers.error ? <ErrorState error={providers.error} retry={providers.reload} /> : providers.data?.items.length ? <div className="provider-grid">{providers.data.items.map((provider) => <Card key={provider.id} className="provider-card">
      <CardHeader title={provider.id} description={`${provider.protocol} · ${provider.authKind}`} action={<StatusBadge status={provider.credential.configured ? 'configured' : 'pending'} />} />
      <div className="provider-url"><code>{provider.baseUrl}</code></div>
      <dl className="definitions"><div className="definition"><dt>Models</dt><dd>{provider.models.map((model) => model.id).join(', ')}</dd></div><div className="definition"><dt>Credential</dt><dd>{provider.credential.ref ?? (provider.credential.configured ? 'Native OAuth credential' : 'Not configured')}</dd></div>{provider.credential.configuredAt && <div className="definition"><dt>Configured</dt><dd>{dateTime(provider.credential.configuredAt)}</dd></div>}</dl>
      <div className="binding-list">{provider.bindings.map((binding) => <div key={`${binding.instanceId}:${binding.runtime}`}><span>{binding.instanceId} / {binding.runtime}</span><StatusBadge status={binding.state} /></div>)}</div>
      <div className="form-actions"><button className="button secondary" disabled={action.busy} onClick={() => refresh('Protocol, authentication, and model catalog validation passed; no task or billable turn was run.', () => api.validateProvider(instance, provider.id))}><RefreshCw size={14} /> Validate</button><button className="button secondary" disabled={!canWrite || action.busy} onClick={() => setCredentialFor(provider)}><KeyRound size={14} /> Rotate credential</button></div>
    </Card>)}</div> : <EmptyState title="No providers" description="No logical Provider registry entries were returned." />}
    <Card><CardHeader title="Runtime binding" description="Projection state is explicit; unsupported native settings are never silently dropped" />
      <div className="binding-form"><label className="field"><span>Provider</span><select value={binding.providerId} onChange={(e) => setBinding({ ...binding, providerId: e.target.value })} disabled={!canWrite}>{providers.data?.items.map((provider) => <option key={provider.id}>{provider.id}</option>)}</select></label><label className="field"><span>Runtime</span><select value={binding.runtime} onChange={(e) => setBinding({ ...binding, runtime: e.target.value })} disabled={!canWrite}>{['pi', 'durable', 'dsh', 'codex', 'claude'].map((x) => <option key={x}>{x}</option>)}</select></label><label className="field"><span>Model</span><input value={binding.model} onChange={(e) => setBinding({ ...binding, model: e.target.value })} disabled={!canWrite} /></label><button className="button primary" disabled={!canWrite || action.busy} onClick={() => refresh('Binding saved with an explicit Runtime projection state.', () => api.bindProvider(instance, binding.runtime, binding.providerId, binding.model))}>Save binding</button></div>
    </Card>
    {credentialFor && <ConfirmDialog title={`Rotate credential for ${credentialFor.id}`} confirmLabel="Write mock credential" busy={action.busy} onClose={() => { setCredentialFor(null); setSecret('') }} onConfirm={() => refresh('Credential reference updated. The secret was discarded from the form and is never returned by GET.', () => api.saveCredential(instance, credentialFor.id, secret))}><div className="notice warning"><KeyRound size={17} /><div><strong>Write-only secret</strong><span>This mock stores only a boolean and opaque reference. Production must validate before switching references.</span></div></div><label className="field"><span>New API key</span><input type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Not logged or returned" /></label></ConfirmDialog>}
    {addOpen && <ConfirmDialog title="Add logical provider" confirmLabel="Create provider" busy={action.busy} onClose={() => setAddOpen(false)} onConfirm={() => refresh('Logical Provider created without embedding a secret.', () => api.createProvider(instance, { ...newProvider, models: newProvider.models.split(',').map((x) => x.trim()).filter(Boolean) }))}><div className="form-stack"><label className="field"><span>Provider ID</span><input value={newProvider.id} onChange={(e) => setNewProvider({ ...newProvider, id: e.target.value })} /></label><label className="field"><span>Protocol</span><select value={newProvider.protocol} onChange={(e) => setNewProvider({ ...newProvider, protocol: e.target.value })}><option>openai-compatible</option><option>anthropic</option></select></label><label className="field"><span>Base URL</span><input value={newProvider.baseUrl} onChange={(e) => setNewProvider({ ...newProvider, baseUrl: e.target.value })} /></label><label className="field"><span>Models (comma separated)</span><input value={newProvider.models} onChange={(e) => setNewProvider({ ...newProvider, models: e.target.value })} /></label></div></ConfirmDialog>}
  </div>
}
