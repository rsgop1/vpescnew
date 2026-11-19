import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'

export default function Middlemen() {
  const qc = useQueryClient()
  const [form, setForm] = useState({ tg_id: '', username: '' })
  const [err, setErr] = useState('')

  const { data: mms = [], isLoading } = useQuery({
    queryKey: ['middlemen'],
    queryFn: () => api.get('/middlemen').then(r => r.data),
    refetchInterval: 15000,
  })

  const addMM = useMutation({
    mutationFn: (payload) => api.post('/middlemen', payload),
    onSuccess: () => {
      qc.invalidateQueries(['middlemen'])
      setForm({ tg_id: '', username: '' })
      setErr('')
    },
    onError: (e) => setErr(e.response?.data?.detail || 'Failed to add middleman'),
  })

  const toggleMM = useMutation({
    mutationFn: ({ tg_id, active }) => api.post(`/middlemen/${tg_id}/toggle`, { active }),
    onSuccess: () => qc.invalidateQueries(['middlemen']),
  })

  const deleteMM = useMutation({
    mutationFn: (tg_id) => api.delete(`/middlemen/${tg_id}`),
    onSuccess: () => qc.invalidateQueries(['middlemen']),
  })

  const handleAdd = (e) => {
    e.preventDefault()
    if (!form.tg_id) { setErr('Telegram ID is required'); return }
    setErr('')
    addMM.mutate({ tg_id: parseInt(form.tg_id), username: form.username })
  }

  const active = mms.filter(m => m.active)
  const inactive = mms.filter(m => !m.active)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div className="card">
        <div className="card-header">
          <h2 className="card-title">🛡️ Middlemen</h2>
          <span style={{ color: 'var(--muted)', fontSize: 13 }}>
            {active.length} active / {mms.length} total
          </span>
        </div>

        <form onSubmit={handleAdd} style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
          <input
            className="input"
            placeholder="Telegram ID *"
            value={form.tg_id}
            onChange={e => setForm(f => ({ ...f, tg_id: e.target.value }))}
            style={{ width: 160 }}
            type="number"
          />
          <input
            className="input"
            placeholder="Username (optional)"
            value={form.username}
            onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
            style={{ width: 200 }}
          />
          <button className="btn btn-primary" type="submit" disabled={addMM.isPending}>
            {addMM.isPending ? 'Adding...' : '+ Add Middleman'}
          </button>
        </form>
        {err && <div style={{ color: 'var(--danger)', fontSize: 13, marginBottom: 8 }}>{err}</div>}
      </div>

      {isLoading ? (
        <div className="card" style={{ textAlign: 'center', color: 'var(--muted)' }}>Loading...</div>
      ) : mms.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', color: 'var(--muted)', padding: 40 }}>
          No middlemen added yet. Use the form above to add the first one.
        </div>
      ) : (
        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>Telegram ID</th>
                <th>Username</th>
                <th>Status</th>
                <th>Added By</th>
                <th>Added At</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {mms.map(mm => (
                <tr key={mm.tg_id}>
                  <td><code style={{ fontSize: 12 }}>{mm.tg_id}</code></td>
                  <td>
                    {mm.username ? (
                      <a
                        href={`https://t.me/${mm.username}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: 'var(--accent)', textDecoration: 'none' }}
                      >
                        @{mm.username}
                      </a>
                    ) : (
                      <span style={{ color: 'var(--muted)' }}>—</span>
                    )}
                  </td>
                  <td>
                    <span style={{
                      padding: '2px 10px',
                      borderRadius: 12,
                      fontSize: 12,
                      fontWeight: 600,
                      background: mm.active ? 'rgba(0,230,118,0.12)' : 'rgba(255,61,87,0.12)',
                      color: mm.active ? 'var(--success)' : 'var(--danger)',
                    }}>
                      {mm.active ? '● Active' : '○ Inactive'}
                    </span>
                  </td>
                  <td style={{ color: 'var(--muted)', fontSize: 13 }}>{mm.added_by || '—'}</td>
                  <td style={{ color: 'var(--muted)', fontSize: 12 }}>
                    {mm.added_at ? new Date(mm.added_at).toLocaleString() : '—'}
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button
                        className={`btn btn-sm ${mm.active ? 'btn-danger' : 'btn-primary'}`}
                        style={{ fontSize: 12, padding: '4px 12px' }}
                        onClick={() => toggleMM.mutate({ tg_id: mm.tg_id, active: !mm.active })}
                        disabled={toggleMM.isPending}
                      >
                        {mm.active ? 'Disable' : 'Enable'}
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        style={{ fontSize: 12, padding: '4px 12px' }}
                        onClick={() => {
                          if (window.confirm(`Remove middleman ${mm.tg_id}?`)) {
                            deleteMM.mutate(mm.tg_id)
                          }
                        }}
                        disabled={deleteMM.isPending}
                      >
                        Remove
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h3 className="card-title" style={{ fontSize: 15 }}>📋 How Middleman Deals Work</h3>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
          {[
            { step: '1', title: 'Buyer Selects MM', desc: 'During deal creation, buyer chooses "Middleman" as payment method and clicks "Pay via Middleman".' },
            { step: '2', title: 'MM Enters Deal', desc: 'Active middlemen get a DM notification. First to click "Enter This Deal" takes the deal.' },
            { step: '3', title: 'MM Sends UPI', desc: 'Middleman types their UPI ID. Buyer receives payment instructions via DM.' },
            { step: '4', title: 'Buyer Pays MM', desc: 'Buyer sends money to MM\'s UPI. MM confirms receipt. Group card updates.' },
            { step: '5', title: 'Buyer Releases', desc: 'After seller delivers product, buyer clicks Release in group. Seller gets notified.' },
            { step: '6', title: 'MM Pays Seller', desc: 'Seller provides UPI. MM sends money. Seller confirms receipt. Deal closes.' },
          ].map(s => (
            <div key={s.step} style={{
              padding: 16,
              background: 'var(--card-bg)',
              borderRadius: 10,
              border: '1px solid var(--border)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                <div style={{
                  width: 28, height: 28, borderRadius: '50%',
                  background: 'var(--accent)', color: '#000',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontWeight: 700, fontSize: 14, flexShrink: 0,
                }}>
                  {s.step}
                </div>
                <strong style={{ fontSize: 14 }}>{s.title}</strong>
              </div>
              <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>{s.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
