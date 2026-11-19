import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

export default function Guillotine() {
  const [targetId, setTargetId] = useState('')
  const [bulkIds, setBulkIds] = useState('')
  const [blacklistVal, setBlacklistVal] = useState('')
  const [blacklistType, setBlacklistType] = useState('tg_id')
  const [blacklistReason, setBlacklistReason] = useState('')
  const [tab, setTab] = useState('actions')
  const qc = useQueryClient()

  const { data: blacklist = [] } = useQuery({
    queryKey: ['blacklist'],
    queryFn: () => api.get('/blacklist').then(r => r.data),
  })

  const { data: reports = [] } = useQuery({
    queryKey: ['scam-reports'],
    queryFn: () => api.get('/scam-reports').then(r => r.data),
  })

  const mut = (fn, msg) => useMutation({
    mutationFn: fn,
    onSuccess: () => { toast.success(msg); qc.invalidateQueries() },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const freezeMut = mut((id) => api.post(`/users/${id}/freeze`), '🧊 User access frozen!')
  const unfreezeMut = mut((id) => api.post(`/users/${id}/unfreeze`), '✅ User access restored!')
  const flagMut = mut((id) => api.post(`/users/${id}/flag-scam`), '🚨 Scam flag set!')
  const unflagMut = mut((id) => api.post(`/users/${id}/unflag-scam`), '✅ Scam flag removed!')
  const wipeMut = mut((id) => api.post(`/users/${id}/wipe-rep`), '🗑️ Reputation wiped!')
  const bulkMut = mut((ids) => api.post('/users/bulk-freeze', { tg_ids: ids }), '🧊 Bulk freeze done!')
  const addBlMut = mut(() => api.post('/blacklist', { value: blacklistVal, type: blacklistType, reason: blacklistReason }), '🛑 Added to blacklist!')
  const delBlMut = mut((val) => api.delete(`/blacklist/${encodeURIComponent(val)}`), '✅ Removed from blacklist!')

  const act = (label, fn, color = 'btn-warning', disabled = false) => (
    <button
      className={`btn ${color}`}
      onClick={() => { if (targetId) fn(parseInt(targetId) || targetId) }}
      disabled={disabled || !targetId}
    >
      {label}
    </button>
  )

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">☠️ Guillotine — Scammer Control</div>
          <div className="section-sub">Nuclear options for bad actors</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {['actions', 'blacklist', 'reports'].map(t => (
            <button key={t} className={`btn ${tab === t ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab(t)}>
              {t === 'actions' ? '⚡ Actions' : t === 'blacklist' ? `🛑 Blacklist (${blacklist.length})` : `📋 Reports (${reports.length})`}
            </button>
          ))}
        </div>
      </div>

      {tab === 'actions' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>🎯 Single Target Actions</div>
            <div className="form-group">
              <label className="form-label">Telegram ID</label>
              <input type="number" placeholder="e.g. 6719245696" value={targetId} onChange={e => setTargetId(e.target.value)} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {act('🧊 Freeze User', (id) => freezeMut.mutate(id), 'btn-warning')}
              {act('✅ Unfreeze', (id) => unfreezeMut.mutate(id), 'btn-primary')}
              {act('🚨 Flag Scam', (id) => flagMut.mutate(id), 'btn-danger')}
              {act('✅ Unflag', (id) => unflagMut.mutate(id), 'btn-secondary')}
              {act('🗑️ Wipe Rep', (id) => wipeMut.mutate(id), 'btn-danger')}
            </div>
          </div>

          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>⚡ Bulk Freeze</div>
            <div className="form-group">
              <label className="form-label">Telegram IDs (comma separated)</label>
              <textarea
                placeholder="123456789, 987654321, 111222333"
                value={bulkIds}
                onChange={e => setBulkIds(e.target.value)}
                style={{ minHeight: 100 }}
              />
            </div>
            <button
              className="btn btn-danger"
              onClick={() => {
                const ids = bulkIds.split(',').map(s => parseInt(s.trim())).filter(Boolean)
                if (ids.length > 0) bulkMut.mutate(ids)
              }}
              disabled={!bulkIds || bulkMut.isPending}
            >
              🧊 Freeze All ({bulkIds.split(',').filter(s => s.trim()).length} users)
            </button>
          </div>
        </div>
      )}

      {tab === 'blacklist' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 20 }}>
          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>🛑 Add to Blacklist</div>
            <div className="form-group">
              <label className="form-label">Type</label>
              <select value={blacklistType} onChange={e => setBlacklistType(e.target.value)}>
                <option value="tg_id">Telegram ID</option>
                <option value="upi">UPI ID</option>
                <option value="ltc">LTC Address</option>
                <option value="username">Username</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Value</label>
              <input placeholder="Enter value to blacklist" value={blacklistVal} onChange={e => setBlacklistVal(e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Reason</label>
              <input placeholder="Why are you blacklisting?" value={blacklistReason} onChange={e => setBlacklistReason(e.target.value)} />
            </div>
            <button className="btn btn-danger" onClick={() => { addBlMut.mutate(); setBlacklistVal(''); setBlacklistReason('') }} disabled={!blacklistVal || addBlMut.isPending}>
              🛑 Add to Blacklist
            </button>
          </div>

          <div>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Current Blacklist ({blacklist.length})</div>
            {blacklist.length === 0 ? (
              <div className="card"><div className="empty"><div className="empty-icon">🛑</div><div className="empty-text">Blacklist is empty</div></div></div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Value</th><th>Type</th><th>Reason</th><th>Date</th><th>Action</th></tr></thead>
                  <tbody>
                    {blacklist.map(b => (
                      <tr key={b.id}>
                        <td className="font-mono">{b.value}</td>
                        <td><span className="badge badge-red">{b.type}</span></td>
                        <td className="text-muted">{b.reason || '—'}</td>
                        <td className="text-muted">{b.at?.slice(0, 10)}</td>
                        <td><button className="btn btn-secondary btn-sm" onClick={() => delBlMut.mutate(b.value)}>Remove</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'reports' && (
        <div>
          {reports.length === 0 ? (
            <div className="card"><div className="empty"><div className="empty-icon">📋</div><div className="empty-text">No scam reports</div></div></div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Reporter</th><th>Target</th><th>Reason</th><th>Deal</th><th>Date</th><th>Status</th></tr></thead>
                <tbody>
                  {reports.map(r => (
                    <tr key={r.id}>
                      <td className="font-mono">{r.reporter_id}</td>
                      <td>@{r.target_un} <span className="font-mono text-muted">({r.target_id})</span></td>
                      <td style={{ maxWidth: 200 }}>{r.reason}</td>
                      <td className="font-mono">{r.deal_id || '—'}</td>
                      <td className="text-muted">{r.at?.slice(0, 10)}</td>
                      <td><span className={`badge ${r.status === 'pending' ? 'badge-yellow' : 'badge-green'}`}>{r.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
