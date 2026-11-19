import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

export default function Groups() {
  const [chatId, setChatId] = useState('')
  const [title, setTitle] = useState('')
  const qc = useQueryClient()

  const { data: groups = [], isLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get('/groups').then(r => r.data),
  })

  const addMut = useMutation({
    mutationFn: () => api.post('/groups', { chat_id: chatId, title }),
    onSuccess: () => { toast.success('Group added!'); setChatId(''); setTitle(''); qc.invalidateQueries({ queryKey: ['groups'] }) },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const delMut = useMutation({
    mutationFn: (id) => api.delete(`/groups/${id}`),
    onSuccess: () => { toast.success('Group removed!'); qc.invalidateQueries({ queryKey: ['groups'] }) },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  if (isLoading) return <div className="loading"><div className="spinner" /></div>

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">💬 Whitelisted Groups</div>
          <div className="section-sub">Only these groups can use /escrow</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 20 }}>
        <div className="card">
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>➕ Add Group</div>
          <div className="form-group">
            <label className="form-label">Group Chat ID</label>
            <input type="text" placeholder="-1001234567890" value={chatId} onChange={e => setChatId(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Group Title (optional)</label>
            <input placeholder="e.g. RSG Deals Group" value={title} onChange={e => setTitle(e.target.value)} />
          </div>
          <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} onClick={() => addMut.mutate()} disabled={!chatId || addMut.isPending}>
            ➕ Whitelist Group
          </button>
          <div style={{ marginTop: 16, padding: 12, background: 'var(--surface)', borderRadius: 8, fontSize: 12, color: 'var(--muted)' }}>
            <strong style={{ color: 'var(--text)' }}>How to get Chat ID:</strong>
            <ol style={{ marginTop: 6, paddingLeft: 16, lineHeight: 1.8 }}>
              <li>Add the bot to your group</li>
              <li>Send any message in the group</li>
              <li>Visit: api.telegram.org/bot[TOKEN]/getUpdates</li>
              <li>Find "chat": {'"id":'} -100XXXXXXXXX</li>
            </ol>
          </div>
        </div>

        <div>
          {groups.length === 0 ? (
            <div className="card">
              <div className="empty">
                <div className="empty-icon">💬</div>
                <div className="empty-text">No groups whitelisted yet</div>
              </div>
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Chat ID</th><th>Title</th><th>Status</th><th>Added By</th><th>Date</th><th>Action</th></tr>
                </thead>
                <tbody>
                  {groups.map(g => (
                    <tr key={g.chat_id}>
                      <td className="font-mono">{g.chat_id}</td>
                      <td>{g.title || '—'}</td>
                      <td>
                        {g.active
                          ? <span className="badge badge-green">✅ Active</span>
                          : <span className="badge badge-gray">❌ Inactive</span>}
                      </td>
                      <td className="text-muted">{g.added_by || 'admin'}</td>
                      <td className="text-muted">{g.at?.slice(0, 10)}</td>
                      <td>
                        <button className="btn btn-danger btn-sm" onClick={() => delMut.mutate(g.chat_id)}>Remove</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
