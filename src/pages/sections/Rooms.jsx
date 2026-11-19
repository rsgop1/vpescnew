import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

const statusClass = {
  available: 'badge-green',
  occupied: 'badge-yellow',
  quarantined: 'badge-red',
}

export default function Rooms() {
  const qc = useQueryClient()
  const { data: rooms = [], isLoading } = useQuery({
    queryKey: ['middleman-rooms'],
    queryFn: () => api.get('/middleman-rooms').then(r => r.data),
    refetchInterval: 15000,
  })

  const releaseMut = useMutation({
    mutationFn: (chatId) => api.post(`/middleman-rooms/${chatId}/release`),
    onSuccess: () => {
      toast.success('Room cleanup completed and room released')
      qc.invalidateQueries({ queryKey: ['middleman-rooms'] })
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Room release failed'),
  })

  const quarantineMut = useMutation({
    mutationFn: (chatId) => api.post(`/middleman-rooms/${chatId}/quarantine`, { reason: 'Admin action' }),
    onSuccess: () => {
      toast.success('Room quarantined')
      qc.invalidateQueries({ queryKey: ['middleman-rooms'] })
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Could not quarantine room'),
  })
  const cleanMut = useMutation({
    mutationFn: ({ chatId, acknowledge }) => api.post(`/middleman-rooms/${chatId}/clean`, {
      acknowledge_dispute_cleanup: acknowledge,
    }),
    onSuccess: (r) => {
      toast.success(r.data?.transcript_preserved
        ? 'Disputed room cleaned; database transcript preserved'
        : 'Room cleaned and released')
      qc.invalidateQueries({ queryKey: ['middleman-rooms'] })
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Could not clean room'),
  })

  if (isLoading) return <div className="loading"><div className="spinner" /></div>

  const counts = rooms.reduce((acc, room) => {
    acc[room.status] = (acc[room.status] || 0) + 1
    return acc
  }, {})

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">🚪 Private Room Pool</div>
          <div className="section-sub">Reusable deal rooms leased to one manual-middleman deal at a time</div>
        </div>
        <div style={{ display: 'flex', gap: 8, fontSize: 12 }}>
          <span className="badge badge-green">{counts.available || 0} available</span>
          <span className="badge badge-yellow">{counts.occupied || 0} occupied</span>
          <span className="badge badge-red">{counts.quarantined || 0} quarantined</span>
        </div>
      </div>

      {rooms.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon">🚪</div>
            <div className="empty-text">No private rooms registered yet</div>
            <div className="text-muted" style={{ marginTop: 8 }}>
              Run <code>/registerroom</code> in each private Telegram group you want to add.
            </div>
          </div>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Room</th>
                <th>Status</th>
                <th>Active Deal</th>
                <th>Participants</th>
                <th>Updated</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rooms.map(room => {
                const deal = room.active_deal
                return (
                  <tr key={room.chat_id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{room.title || 'Untitled room'}</div>
                      <div className="font-mono text-muted">{room.chat_id}</div>
                    </td>
                    <td>
                      <span className={`badge ${statusClass[room.status] || 'badge-gray'}`}>
                        {room.status}
                      </span>
                    </td>
                    <td>
                      {deal ? (
                        <div>
                          <div className="font-mono">#{deal.deal_id}</div>
                          <div className="text-muted">₹{(deal.amount || 0).toLocaleString()} · {deal.status}</div>
                        </div>
                      ) : '—'}
                    </td>
                    <td>
                      {deal ? (
                        <div style={{ fontSize: 12, lineHeight: 1.6 }}>
                          <div>Buyer: @{deal.buyer_un || deal.buyer_id || '—'}</div>
                          <div>Seller: @{deal.seller_un || deal.seller_id || '—'}</div>
                          <div>MM: @{deal.middleman_un || deal.middleman_id || '—'}</div>
                        </div>
                      ) : '—'}
                    </td>
                    <td className="text-muted">{room.updated_at?.slice(0, 19).replace('T', ' ') || '—'}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 6 }}>
                        {room.status === 'occupied' && (
                          <button
                            className="btn btn-primary btn-sm"
                            onClick={() => releaseMut.mutate(room.chat_id)}
                            disabled={releaseMut.isPending}
                          >
                            Release & clean
                          </button>
                        )}
                        {room.status === 'quarantined' && (
                          <button
                            className="btn btn-primary btn-sm"
                            onClick={() => {
                              const disputed = deal?.status === 'disputed'
                              const message = disputed
                                ? 'This room has a disputed deal. The database transcript is preserved, but Telegram room messages and participant membership will be cleaned. Continue?'
                                : 'Clean Telegram messages and participants, then release this quarantined room?'
                              if (window.confirm(message)) {
                                cleanMut.mutate({ chatId: room.chat_id, acknowledge: disputed })
                              }
                            }}
                            disabled={cleanMut.isPending}
                          >
                            🧹 Clean & release
                          </button>
                        )}
                        {room.status !== 'quarantined' && (
                          <button
                            className="btn btn-danger btn-sm"
                            onClick={() => quarantineMut.mutate(room.chat_id)}
                            disabled={quarantineMut.isPending}
                          >
                            Quarantine
                          </button>
                        )}
                      </div>
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