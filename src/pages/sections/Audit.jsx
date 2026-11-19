import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'

const actionColor = {
  admin_login: 'badge-blue', deal_created: 'badge-green', deal_paid: 'badge-green',
  deal_completed: 'badge-green', deal_cancelled: 'badge-gray', dispute_resolved: 'badge-purple',
  deposit_approved: 'badge-green', deposit_rejected: 'badge-red', withdrawal_paid: 'badge-green',
  user_frozen: 'badge-red', user_unfrozen: 'badge-green', scam_flagged: 'badge-red',
  rep_wiped: 'badge-red', blacklisted: 'badge-red', group_added: 'badge-blue',
  settings_updated: 'badge-yellow', broadcast: 'badge-purple', balance_adjusted: 'badge-yellow',
  treasury_withdraw: 'badge-yellow', force_complete: 'badge-yellow',
}

export default function Audit() {
  const [search, setSearch] = useState('')

  const { data: logs = [], isLoading } = useQuery({
    queryKey: ['audit'],
    queryFn: () => api.get('/audit').then(r => r.data),
    refetchInterval: 20000,
  })

  const filtered = logs.filter(l =>
    !search ||
    l.action?.includes(search) ||
    l.actor?.includes(search) ||
    JSON.stringify(l.details || l.details_json || {}).includes(search)
  )

  if (isLoading) return <div className="loading"><div className="spinner" /></div>

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">📜 Audit Log</div>
          <div className="section-sub">{logs.length} total events (last 300)</div>
        </div>
        <input style={{ width: 240 }} placeholder="Search action / actor..." value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      {filtered.length === 0 ? (
        <div className="card"><div className="empty"><div className="empty-icon">📜</div><div className="empty-text">No audit events found</div></div></div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Time</th><th>Actor</th><th>Action</th><th>Details</th></tr>
            </thead>
            <tbody>
              {filtered.map((l, i) => {
                let details = l.details || {}
                if (typeof details === 'string') {
                  try { details = JSON.parse(details) } catch {}
                }
                return (
                  <tr key={i}>
                    <td className="text-muted font-mono">{l.at?.slice(0, 16)}</td>
                    <td style={{ fontWeight: 500 }}>{l.actor}</td>
                    <td><span className={`badge ${actionColor[l.action] || 'badge-gray'}`}>{l.action?.replace(/_/g, ' ')}</span></td>
                    <td style={{ fontSize: 12, color: 'var(--muted)', maxWidth: 300 }}>
                      {Object.entries(details).slice(0, 3).map(([k, v]) => (
                        <span key={k} style={{ marginRight: 10 }}>
                          <strong>{k}:</strong> {String(v).slice(0, 30)}
                        </span>
                      ))}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
