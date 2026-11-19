import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

const statusColor = {
  completed: 'badge-green', awaiting_payment: 'badge-yellow',
  paid: 'badge-blue', disputed: 'badge-red', cancelled: 'badge-gray',
  awaiting_acceptance: 'badge-gray', release_confirm: 'badge-purple',
}

const STATUSES = ['all', 'awaiting_acceptance', 'awaiting_payment', 'paid', 'completed', 'disputed', 'cancelled']

export default function Deals() {
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState(null)
  const qc = useQueryClient()

  const { data: deals = [], isLoading } = useQuery({
    queryKey: ['deals', filter],
    queryFn: () => api.get('/deals', { params: filter !== 'all' ? { status: filter } : {} }).then(r => r.data),
    refetchInterval: 10000,
  })

  const forceMut = useMutation({
    mutationFn: (id) => api.post(`/deals/${id}/force-complete`),
    onSuccess: () => { toast.success('Deal force-completed!'); qc.invalidateQueries({ queryKey: ['deals'] }); setSelected(null) },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const filtered = deals.filter(d =>
    !search || d.deal_id?.includes(search) || d.buyer_un?.includes(search) || d.seller_un?.includes(search)
  )

  if (isLoading) return <div className="loading"><div className="spinner" /></div>

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">🤝 All Deals</div>
          <div className="section-sub">{deals.length} total deals</div>
        </div>
        <input style={{ width: 220 }} placeholder="Search by ID / username..." value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      <div className="filters">
        {STATUSES.map(s => (
          <button key={s} className={`filter-btn${filter === s ? ' active' : ''}`} onClick={() => setFilter(s)}>
            {s === 'all' ? 'All' : s.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>Buyer</th><th>Seller</th><th>Amount</th><th>Fee</th><th>Status</th><th>Date</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={8}><div className="empty"><div className="empty-icon">🤝</div><div className="empty-text">No deals found</div></div></td></tr>
            )}
            {filtered.map(d => (
              <tr key={d.deal_id}>
                <td className="font-mono">#{d.deal_id}</td>
                <td>@{d.buyer_un || '—'}</td>
                <td>@{d.seller_un || '—'}</td>
                <td style={{ color: 'var(--accent)' }}>₹{(d.amount || 0).toLocaleString()}</td>
                <td className="text-muted">{d.fee_percent}% ({d.fee_payer})</td>
                <td><span className={`badge ${statusColor[d.status] || 'badge-gray'}`}>{d.status?.replace(/_/g, ' ')}</span></td>
                <td className="text-muted">{d.created_at?.slice(0, 10)}</td>
                <td>
                  <button className="btn btn-secondary btn-sm" onClick={() => setSelected(d)}>View</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-title">
              <span>Deal #{selected.deal_id}</span>
              <button className="btn btn-secondary btn-sm" onClick={() => setSelected(null)}>✕</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, fontSize: 13 }}>
              {[
                ['Buyer', `@${selected.buyer_un}`],
                ['Seller', `@${selected.seller_un}`],
                ['Amount', `₹${(selected.amount || 0).toLocaleString()}`],
                ['Status', selected.status],
                ['Fee', `${selected.fee_percent}% (${selected.fee_payer})`],
                ['Created', selected.created_at?.slice(0, 16)],
                ['Expires', selected.expires_at?.slice(0, 16)],
              ].map(([k, v]) => (
                <div key={k}>
                  <div className="text-muted">{k}</div>
                  <div style={{ marginTop: 2 }}>{v}</div>
                </div>
              ))}
            </div>
            {selected.toc && (
              <div style={{ marginTop: 12, padding: '10px', background: 'var(--surface)', borderRadius: 6, fontSize: 12 }}>
                <div className="text-muted" style={{ marginBottom: 4 }}>Terms & Conditions</div>
                <div>{selected.toc}</div>
              </div>
            )}
            <div className="modal-actions">
              {['paid', 'release_confirm', 'disputed'].includes(selected.status) && (
                <button className="btn btn-warning" onClick={() => forceMut.mutate(selected.deal_id)} disabled={forceMut.isPending}>
                  ⚡ Force Complete
                </button>
              )}
              <button className="btn btn-secondary" onClick={() => setSelected(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
