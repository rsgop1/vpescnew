import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

export default function Settings() {
  const [fee, setFee] = useState('')
  const [maint, setMaint] = useState(false)
  const [testModeAdminOnly, setTestModeAdminOnly] = useState(false)
  const [defaultToc, setDefaultToc] = useState('')
  const [broadcastText, setBroadcastText] = useState('')
  const [tmplName, setTmplName] = useState('')
  const [tmplText, setTmplText] = useState('')
  const [tab, setTab] = useState('system')
  const qc = useQueryClient()

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get('/settings').then(r => r.data),
  })

  const { data: bcastTmpls = [] } = useQuery({
    queryKey: ['broadcast-templates'],
    queryFn: () => api.get('/broadcast-templates').then(r => r.data),
  })

  useEffect(() => {
    if (settings) {
      setFee(settings.fee_percent)
      setMaint(settings.maintenance_mode)
      setTestModeAdminOnly(settings.test_mode_admin_only)
      setDefaultToc(settings.default_toc || '')
    }
  }, [settings])

  const saveMut = useMutation({
    mutationFn: (data) => api.post('/settings', data),
    onSuccess: () => { toast.success('Settings saved!'); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const broadcastMut = useMutation({
    mutationFn: () => api.post('/broadcast', { text: broadcastText }),
    onSuccess: (res) => { toast.success(`✅ Broadcast sent to ${res.data.sent} users!`); setBroadcastText('') },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const addTmplMut = useMutation({
    mutationFn: () => api.post('/broadcast-templates', { name: tmplName, text: tmplText }),
    onSuccess: () => { toast.success('Template saved!'); setTmplName(''); setTmplText(''); qc.invalidateQueries({ queryKey: ['broadcast-templates'] }) },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const delTmplMut = useMutation({
    mutationFn: (id) => api.delete(`/broadcast-templates/${id}`),
    onSuccess: () => { toast.success('Template deleted!'); qc.invalidateQueries({ queryKey: ['broadcast-templates'] }) },
  })

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">⚙️ System Controls</div>
          <div className="section-sub">Live configuration without touching backend code</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {['system', 'broadcast'].map(t => (
            <button key={t} className={`btn ${tab === t ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab(t)}>
              {t === 'system' ? '⚙️ System' : '📢 Broadcast'}
            </button>
          ))}
        </div>
      </div>

      {tab === 'system' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>💰 Fee Configuration</div>
            <div className="form-group">
              <label className="form-label">Bot Fee % (current: {settings?.fee_percent}%)</label>
              <input
                type="number"
                step="0.1"
                min="0"
                max="10"
                placeholder="1.0"
                value={fee}
                onChange={e => setFee(e.target.value)}
              />
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>Range: 0% – 10%</div>
            </div>
            <button className="btn btn-primary" onClick={() => saveMut.mutate({ fee_percent: parseFloat(fee) })} disabled={saveMut.isPending}>
              💾 Save Fee
            </button>
          </div>

          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>🧪 Test Mode Access</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
              <div
                role="switch"
                aria-checked={testModeAdminOnly}
                tabIndex={0}
                onClick={() => setTestModeAdminOnly(!testModeAdminOnly)}
                onKeyDown={e => e.key === 'Enter' && setTestModeAdminOnly(!testModeAdminOnly)}
                style={{
                  width: 48, height: 26, borderRadius: 13, cursor: 'pointer',
                  background: testModeAdminOnly ? 'var(--accent3)' : 'var(--border)',
                  position: 'relative', transition: 'background 0.2s',
                }}
              >
                <div style={{
                  position: 'absolute', top: 3, left: testModeAdminOnly ? 26 : 3,
                  width: 20, height: 20, borderRadius: '50%',
                  background: 'white', transition: 'left 0.2s',
                }} />
              </div>
              <span style={{ fontSize: 14 }}>
                {testModeAdminOnly ? '🧪 Admin-only test mode ON' : '✅ Public deal creation ON'}
              </span>
            </div>
            <button
              className={`btn ${testModeAdminOnly ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => saveMut.mutate({ test_mode_admin_only: testModeAdminOnly })}
              disabled={saveMut.isPending}
            >
              {testModeAdminOnly ? '🔒 Keep Admin-only Mode' : '🌐 Allow User Deals'}
            </button>
            <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 10 }}>
              When enabled, only the configured Telegram admin can start new test deals with /escrow.
            </p>
          </div>

          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>🚧 Maintenance Mode</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
              <div
                onClick={() => setMaint(!maint)}
                style={{
                  width: 48, height: 26, borderRadius: 13, cursor: 'pointer',
                  background: maint ? 'var(--danger)' : 'var(--border)',
                  position: 'relative', transition: 'background 0.2s',
                }}
              >
                <div style={{
                  position: 'absolute', top: 3, left: maint ? 26 : 3,
                  width: 20, height: 20, borderRadius: '50%',
                  background: 'white', transition: 'left 0.2s',
                }} />
              </div>
              <span style={{ fontSize: 14 }}>
                {maint ? '🚧 Maintenance ON — New deals blocked' : '✅ System Online — All features active'}
              </span>
            </div>
            <button
              className={`btn ${maint ? 'btn-danger' : 'btn-primary'}`}
              onClick={() => saveMut.mutate({ maintenance_mode: maint })}
              disabled={saveMut.isPending}
            >
              {maint ? '🚧 Enable Maintenance Mode' : '✅ Keep System Online'}
            </button>
            <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 10 }}>
              When enabled: new deals blocked, existing deals continue processing.
            </p>
          </div>

          <div className="card" style={{ gridColumn: '1 / -1' }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>📋 Default Terms of Service</div>
            <div className="form-group">
              <label className="form-label">Pre-filled ToS shown to users during deal creation</label>
              <textarea
                placeholder="e.g. All sales are final. Buyer must confirm delivery within 24h. Scamming will result in permanent ban."
                value={defaultToc}
                onChange={e => setDefaultToc(e.target.value)}
                style={{ minHeight: 100, fontFamily: 'monospace', fontSize: 12 }}
              />
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
                Users can edit these during deal creation. Leave blank to skip pre-fill.
              </div>
            </div>
            <button
              className="btn btn-primary"
              onClick={() => saveMut.mutate({ default_toc: defaultToc })}
              disabled={saveMut.isPending}
            >
              💾 Save Default ToS
            </button>
          </div>
        </div>
      )}

      {tab === 'broadcast' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>📢 Send Broadcast</div>
            <div className="form-group">
              <label className="form-label">Message</label>
              <textarea
                placeholder="Write your broadcast message here..."
                value={broadcastText}
                onChange={e => setBroadcastText(e.target.value)}
                style={{ minHeight: 120 }}
              />
            </div>
            <button className="btn btn-primary" onClick={() => broadcastMut.mutate()} disabled={!broadcastText || broadcastMut.isPending} style={{ width: '100%', justifyContent: 'center' }}>
              {broadcastMut.isPending ? '📤 Sending...' : '📢 Send to All Users'}
            </button>
          </div>

          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>📋 Broadcast Templates</div>
            <div style={{ marginBottom: 14 }}>
              <input placeholder="Template name" value={tmplName} onChange={e => setTmplName(e.target.value)} style={{ marginBottom: 8 }} />
              <textarea
                placeholder="Template text..."
                value={tmplText}
                onChange={e => setTmplText(e.target.value)}
                style={{ minHeight: 70 }}
              />
              <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={() => addTmplMut.mutate()} disabled={!tmplName || !tmplText}>
                💾 Save Template
              </button>
            </div>

            {bcastTmpls.length === 0 ? (
              <div style={{ fontSize: 13, color: 'var(--muted)', fontStyle: 'italic' }}>No saved templates</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {bcastTmpls.map(t => (
                  <div key={t.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', padding: '8px 10px', background: 'var(--surface)', borderRadius: 6 }}>
                    <div>
                      <div style={{ fontWeight: 500, fontSize: 13 }}>{t.name}</div>
                      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{t.text?.slice(0, 60)}...</div>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-primary btn-sm" onClick={() => setBroadcastText(t.text)}>Use</button>
                      <button className="btn btn-danger btn-sm" onClick={() => delTmplMut.mutate(t.id)}>Del</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
