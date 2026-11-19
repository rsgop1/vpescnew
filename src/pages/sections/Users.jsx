import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'

export default function Users() {
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState(null)

  const { data: users = [], isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get('/users').then(r => r.data),
    refetchInterval: 30000,
  })

  const filtered = users.filter(u =>
    !search ||
    u.username?.toLowerCase().includes(search.toLowerCase()) ||
    String(u.tg_id).includes(search)
  )

  const tierBadge = { new: 'badge-gray', regular: 'badge-blue', trusted: 'badge-purple', elite: 'badge-yellow' }
  const tierIcon = { new: '🌱', regular: '⭐', trusted: '💫', elite: '👑' }

  if (isLoading) return <div className="loading"><div className="spinner" /></div>

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">👥 All Users</div>
          <div className="section-sub">{users.length} registered users</div>
        </div>
        <input style={{ width: 240 }} placeholder="Search username or Telegram ID..." value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>User</th><th>Deals</th><th>Volume</th><th>Rating</th><th>Tier</th><th>Status</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={7}><div className="empty"><div className="empty-icon">👥</div><div className="empty-text">No users found</div></div></td></tr>
            )}
            {filtered.map(u => {
              const rating = u.rating_count > 0 ? (u.rating_sum / u.rating_count).toFixed(1) : '—'
              let badges = []
              try { badges = JSON.parse(u.badges || '[]') } catch {}
              return (
                <tr key={u.tg_id}>
                  <td>
                    <div style={{ fontWeight: 500 }}>@{u.username || '—'}</div>
                    <div className="font-mono text-muted">{u.tg_id}</div>
                  </td>
                  <td>{u.total_deals || 0}</td>
                  <td>₹{(u.total_volume || 0).toLocaleString()}</td>
                  <td>{rating === '—' ? '—' : `${rating}⭐`}</td>
                  <td>
                    <span className={`badge ${tierBadge[u.tier] || 'badge-gray'}`}>
                      {tierIcon[u.tier] || '🌱'} {u.tier || 'new'}
                    </span>
                  </td>
                  <td>
                    {u.frozen && <span className="badge badge-blue">🧊 Frozen</span>}
                    {u.scam_flag && <span className="badge badge-red">🚨 Scam</span>}
                    {!u.frozen && !u.scam_flag && <span className="badge badge-green">✅ Active</span>}
                  </td>
                  <td>
                    <button className="btn btn-secondary btn-sm" onClick={() => setSelected(u)}>⚙️</button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="modal" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
            <div className="modal-title">
              <span>⚙️ @{selected.username}</span>
              <button className="btn btn-secondary btn-sm" onClick={() => setSelected(null)}>✕</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, fontSize: 13, marginBottom: 16 }}>
              {[
                ['Telegram ID', selected.tg_id],
                ['Total Deals', selected.total_deals || 0],
                ['Total Volume', `₹${(selected.total_volume || 0).toLocaleString()}`],
                ['Vouches', selected.vouches || 0],
                ['Tier', `${selected.tier || 'new'}`],
                ['Member since', selected.created_at?.slice(0, 10)],
              ].map(([k, v]) => (
                <div key={k}><div className="text-muted">{k}</div><div>{v}</div></div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
