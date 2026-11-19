import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

export default function Templates() {
  const [name, setName] = useState('')
  const [toc, setToc] = useState('')
  const [amount, setAmount] = useState('')
  const qc = useQueryClient()

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ['templates'],
    queryFn: () => api.get('/templates').then(r => r.data),
  })

  const addMut = useMutation({
    mutationFn: () => api.post('/templates', { name, toc, amount: parseInt(amount) || 0 }),
    onSuccess: () => { toast.success('Template added!'); setName(''); setToc(''); setAmount(''); qc.invalidateQueries({ queryKey: ['templates'] }) },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const delMut = useMutation({
    mutationFn: (id) => api.delete(`/templates/${id}`),
    onSuccess: () => { toast.success('Template deleted!'); qc.invalidateQueries({ queryKey: ['templates'] }) },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const EXAMPLES = [
    { name: '🎮 Gaming Account', toc: '30-day warranty. Working credentials guaranteed.', amount: 500 },
    { name: '📺 Netflix Premium', toc: '1 month. Screen will be assigned. No password change.', amount: 299 },
    { name: '💻 Freelance Work', toc: 'Delivery within agreed timeline. Revisions included.', amount: 2000 },
    { name: '🎵 Spotify Premium', toc: '1 month family plan slot.', amount: 99 },
  ]

  if (isLoading) return <div className="loading"><div className="spinner" /></div>

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">📋 Deal Templates</div>
          <div className="section-sub">Quick deal creation with pre-set terms</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 20 }}>
        <div className="card">
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>➕ Create Template</div>
          <div className="form-group">
            <label className="form-label">Template Name</label>
            <input placeholder="e.g. Netflix 1 Month" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Default Amount (₹)</label>
            <input type="number" placeholder="0 = flexible" value={amount} onChange={e => setAmount(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Terms & Conditions</label>
            <textarea
              placeholder="Pre-set terms for this deal type..."
              value={toc}
              onChange={e => setToc(e.target.value)}
            />
          </div>
          <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} onClick={() => addMut.mutate()} disabled={!name || addMut.isPending}>
            ➕ Add Template
          </button>

          <div style={{ marginTop: 20 }}>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>Quick add examples:</div>
            {EXAMPLES.map(ex => (
              <button
                key={ex.name}
                className="btn btn-secondary btn-sm"
                style={{ marginBottom: 6, width: '100%', justifyContent: 'flex-start' }}
                onClick={() => { setName(ex.name); setToc(ex.toc); setAmount(String(ex.amount)) }}
              >
                {ex.name}
              </button>
            ))}
          </div>
        </div>

        <div>
          {templates.length === 0 ? (
            <div className="card">
              <div className="empty">
                <div className="empty-icon">📋</div>
                <div className="empty-text">No templates yet. Create one to speed up deal creation!</div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {templates.map(t => (
                <div key={t.id} className="card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{t.name}</div>
                    <button className="btn btn-danger btn-sm" onClick={() => delMut.mutate(t.id)}>✕</button>
                  </div>
                  {t.amount > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <span className="badge badge-green">₹{(t.amount || 0).toLocaleString()}</span>
                    </div>
                  )}
                  {t.toc && (
                    <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>
                      {t.toc}
                    </div>
                  )}
                  <div style={{ marginTop: 10, fontSize: 11, color: 'var(--muted)' }}>
                    Added by {t.by} · {t.at?.slice(0, 10)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
