import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

function ScreenshotModal({ txId, onClose }) {
  const { data, isLoading } = useQuery({
    queryKey: ['screenshot', txId],
    queryFn: () => api.get(`/deposits/${txId}/screenshot`).then(r => r.data),
    enabled: !!txId,
  })

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 9999, padding: 24,
    }} onClick={onClose}>
      <div style={{
        background: 'var(--card)', borderRadius: 16, padding: 24,
        maxWidth: 480, width: '100%', boxShadow: '0 8px 40px rgba(0,0,0,0.6)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 16 }}>📸 Deposit Proof</div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', color: 'var(--muted)',
            cursor: 'pointer', fontSize: 20, lineHeight: 1,
          }}>✕</button>
        </div>
        {isLoading ? (
          <div className="loading"><div className="spinner" /></div>
        ) : data ? (
          <div>
            <div style={{ display: 'grid', gap: 8, marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span className="text-muted">User:</span>
                <strong>@{data.username || data.tg_id}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span className="text-muted">Amount:</span>
                <strong style={{ color: 'var(--accent)' }}>{(data.amount || 0).toLocaleString()} VC</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span className="text-muted">Status:</span>
                <span style={{
                  padding: '2px 10px', borderRadius: 20, fontSize: 12,
                  background: data.status === 'pending' ? 'rgba(255,170,0,0.15)' : 'rgba(0,255,136,0.15)',
                  color: data.status === 'pending' ? '#ffaa00' : 'var(--accent)',
                }}>
                  {data.status?.toUpperCase()}
                </span>
              </div>
            </div>
            {data.photo_file_id ? (
              <div style={{
                background: 'var(--surface)', borderRadius: 10, padding: 16,
                textAlign: 'center', border: '1px dashed var(--border)',
              }}>
                <div style={{ fontSize: 40, marginBottom: 8 }}>📷</div>
                <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 8 }}>
                  Screenshot received from user via Telegram
                </div>
                <div style={{
                  fontSize: 11, fontFamily: 'monospace', padding: '6px 10px',
                  background: 'rgba(0,0,0,0.3)', borderRadius: 6, wordBreak: 'break-all',
                  color: 'var(--accent3)',
                }}>
                  File ID: {data.photo_file_id.slice(0, 40)}...
                </div>
                <div style={{ marginTop: 10, fontSize: 12, color: 'var(--muted)' }}>
                  💡 View the screenshot in your Telegram bot's admin DM
                </div>
              </div>
            ) : data.proof_text ? (
              <div style={{
                background: 'var(--surface)', borderRadius: 10, padding: 12,
                border: '1px solid var(--border)', fontSize: 13,
              }}>
                <div className="text-muted" style={{ marginBottom: 4 }}>📝 Proof Text:</div>
                <div>{data.proof_text}</div>
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: 24, color: 'var(--muted)' }}>
                No proof submitted
              </div>
            )}
          </div>
        ) : (
          <div style={{ color: 'var(--muted)', textAlign: 'center' }}>Failed to load</div>
        )}
      </div>
    </div>
  )
}

function RejectReasonForm({ label, onConfirm, onCancel, isPending }) {
  const [reason, setReason] = useState('')
  return (
    <div style={{
      background: 'rgba(255,80,80,0.07)', borderRadius: 10, padding: 12,
      border: '1px solid rgba(255,80,80,0.2)', marginTop: 4,
    }}>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
        {label} — enter a reason (optional):
      </div>
      <input
        className="input"
        style={{ marginBottom: 8, fontSize: 13 }}
        placeholder="e.g. Invalid screenshot, incorrect amount..."
        value={reason}
        onChange={e => setReason(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') onConfirm(reason) }}
        autoFocus
      />
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          className="btn btn-danger"
          style={{ flex: 1, fontSize: 12 }}
          onClick={() => onConfirm(reason)}
          disabled={isPending}
        >
          {isPending ? 'Rejecting...' : '❌ Confirm Reject'}
        </button>
        <button
          className="btn btn-secondary"
          style={{ fontSize: 12 }}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

export default function Financial() {
  const [tab, setTab] = useState('deposits')
  const [screenshotTx, setScreenshotTx] = useState(null)
  const [rejectingDepId, setRejectingDepId] = useState(null)
  const [rejectingWdId, setRejectingWdId] = useState(null)
  const [rejectingVouchId, setRejectingVouchId] = useState(null)
  const qc = useQueryClient()

  const { data: deposits = [], isLoading: dLoad } = useQuery({
    queryKey: ['deposits'],
    queryFn: () => api.get('/deposits/pending').then(r => r.data),
    refetchInterval: 8000,
  })

  const { data: withdrawals = [], isLoading: wLoad } = useQuery({
    queryKey: ['withdrawals'],
    queryFn: () => api.get('/withdrawals/pending').then(r => r.data),
    refetchInterval: 8000,
  })

  const { data: vouches = [], isLoading: vLoad } = useQuery({
    queryKey: ['vouches_pending'],
    queryFn: () => api.get('/vouches/pending').then(r => r.data),
    refetchInterval: 12000,
  })

  const approveDep = useMutation({
    mutationFn: (id) => api.post(`/deposits/${id}/approve`),
    onSuccess: (_, id) => {
      toast.success('✅ Deposit approved! VC credited & user notified.')
      qc.invalidateQueries({ queryKey: ['deposits'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      if (screenshotTx === id) setScreenshotTx(null)
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Failed to approve'),
  })

  const rejectDep = useMutation({
    mutationFn: ({ id, reason }) => api.post(`/deposits/${id}/reject`, { reason }),
    onSuccess: () => {
      toast.success('Deposit rejected. User notified.')
      qc.invalidateQueries({ queryKey: ['deposits'] })
      setRejectingDepId(null)
      if (screenshotTx) setScreenshotTx(null)
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Failed to reject'),
  })

  const paidWd = useMutation({
    mutationFn: (id) => api.post(`/withdrawals/${id}/paid`),
    onSuccess: () => {
      toast.success('✅ Withdrawal marked as paid! User notified.')
      qc.invalidateQueries({ queryKey: ['withdrawals'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const rejectWd = useMutation({
    mutationFn: ({ id, reason }) => api.post(`/withdrawals/${id}/reject`, { reason }),
    onSuccess: () => {
      toast.success('Withdrawal rejected. VC refunded & user notified.')
      qc.invalidateQueries({ queryKey: ['withdrawals'] })
      setRejectingWdId(null)
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const approveVouch = useMutation({
    mutationFn: (id) => api.post(`/vouches/${id}/approve`),
    onSuccess: () => {
      toast.success('✅ Vouch approved! Both parties notified.')
      qc.invalidateQueries({ queryKey: ['vouches_pending'] })
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Failed'),
  })

  const rejectVouch = useMutation({
    mutationFn: ({ id, reason }) => api.post(`/vouches/${id}/reject`, { reason }),
    onSuccess: () => {
      toast.success('Vouch rejected. Voucher notified.')
      qc.invalidateQueries({ queryKey: ['vouches_pending'] })
      setRejectingVouchId(null)
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Failed'),
  })

  const tabs = [
    { key: 'deposits', label: '📥 Deposits', count: deposits.length, color: '#ffaa00' },
    { key: 'withdrawals', label: '📤 Withdrawals', count: withdrawals.length, color: '#ff6464' },
    { key: 'vouches', label: '🤝 Vouches', count: vouches.length, color: '#00aaff' },
  ]

  return (
    <div>
      {screenshotTx && (
        <ScreenshotModal txId={screenshotTx} onClose={() => setScreenshotTx(null)} />
      )}

      <div className="section-header">
        <div>
          <div className="section-title">💰 Financial Queue</div>
          <div className="section-sub">Manage deposits, withdrawals and pending vouches</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {tabs.map(t => (
            <button
              key={t.key}
              className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
              {t.count > 0 && (
                <span style={{
                  marginLeft: 6, borderRadius: 10, padding: '1px 7px', fontSize: 11,
                  background: `${t.color}33`, color: t.color,
                }}>{t.count}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {tab === 'deposits' && (
        <div>
          {dLoad ? (
            <div className="loading"><div className="spinner" /></div>
          ) : deposits.length === 0 ? (
            <div className="card">
              <div className="empty">
                <div className="empty-icon">📥</div>
                <div className="empty-text">No pending deposits</div>
                <div className="text-muted" style={{ marginTop: 6, fontSize: 13 }}>
                  Deposits appear here when users request via /wallet
                </div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {deposits.map(tx => {
                const data = tx.data || {}
                const hasPhoto = !!data.photo_file_id
                return (
                  <div key={tx.id} className="card" style={{
                    display: 'flex', alignItems: 'flex-start',
                    justifyContent: 'space-between', flexWrap: 'wrap', gap: 16,
                  }}>
                    <div style={{ flex: 1, minWidth: 220 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                        <div style={{
                          width: 40, height: 40, borderRadius: '50%',
                          background: 'rgba(0,255,136,0.1)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
                        }}>📥</div>
                        <div>
                          <div style={{ fontWeight: 700, fontSize: 15 }}>
                            @{data.username || `User ${tx.user_id}`}
                          </div>
                          <div className="text-muted" style={{ fontSize: 12 }}>
                            TG ID: {data.tg_id || tx.user_id}
                          </div>
                        </div>
                        {hasPhoto && (
                          <span style={{
                            marginLeft: 6, padding: '2px 8px', borderRadius: 20,
                            background: 'rgba(0,200,255,0.15)', color: '#00c8ff',
                            fontSize: 11, fontWeight: 600,
                          }}>📸 Has Screenshot</span>
                        )}
                      </div>

                      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', fontSize: 13, marginBottom: 8 }}>
                        <div>
                          <span className="text-muted">Amount: </span>
                          <strong style={{ color: 'var(--accent)', fontSize: 16 }}>
                            {(tx.amount || 0).toLocaleString()} VC
                          </strong>
                          <span className="text-muted" style={{ marginLeft: 4 }}>
                            (= ₹{(tx.amount || 0).toLocaleString()})
                          </span>
                        </div>
                        <div>
                          <span className="text-muted">Time: </span>
                          <span>{tx.at?.slice(0, 16).replace('T', ' ')}</span>
                        </div>
                      </div>

                      {(data.proof_text || data.caption) && (
                        <div style={{
                          padding: '8px 12px', background: 'var(--surface)',
                          borderRadius: 8, fontSize: 12, color: 'var(--muted)',
                          border: '1px solid var(--border)', marginBottom: 6,
                        }}>
                          📝 {data.proof_text || data.caption}
                        </div>
                      )}

                      <div className="font-mono" style={{ fontSize: 11, color: 'var(--muted)' }}>
                        TX: {tx.id?.slice(0, 16)}...
                      </div>

                      {rejectingDepId === tx.id && (
                        <RejectReasonForm
                          label="Rejecting deposit"
                          onConfirm={(reason) => rejectDep.mutate({ id: tx.id, reason })}
                          onCancel={() => setRejectingDepId(null)}
                          isPending={rejectDep.isPending}
                        />
                      )}
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 140 }}>
                      {hasPhoto && (
                        <button
                          className="btn btn-secondary"
                          onClick={() => setScreenshotTx(tx.id)}
                          style={{ fontSize: 13 }}
                        >
                          🖼️ View Screenshot
                        </button>
                      )}
                      <button
                        className="btn btn-primary"
                        onClick={() => approveDep.mutate(tx.id)}
                        disabled={approveDep.isPending}
                      >
                        ✅ Approve & Credit
                      </button>
                      {rejectingDepId === tx.id ? null : (
                        <button
                          className="btn btn-danger"
                          onClick={() => setRejectingDepId(tx.id)}
                        >
                          ❌ Reject
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {tab === 'withdrawals' && (
        <div>
          {wLoad ? (
            <div className="loading"><div className="spinner" /></div>
          ) : withdrawals.length === 0 ? (
            <div className="card">
              <div className="empty">
                <div className="empty-icon">📤</div>
                <div className="empty-text">No pending withdrawals</div>
                <div className="text-muted" style={{ marginTop: 6, fontSize: 13 }}>
                  Withdrawal requests appear here when users request via /wallet
                </div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {withdrawals.map(tx => {
                const data = tx.data || {}
                return (
                  <div key={tx.id} className="card" style={{
                    display: 'flex', alignItems: 'flex-start',
                    justifyContent: 'space-between', flexWrap: 'wrap', gap: 16,
                  }}>
                    <div style={{ flex: 1, minWidth: 220 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                        <div style={{
                          width: 40, height: 40, borderRadius: '50%',
                          background: 'rgba(255,100,100,0.1)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
                        }}>📤</div>
                        <div>
                          <div style={{ fontWeight: 700, fontSize: 15 }}>
                            @{data.username || `User ${tx.user_id}`}
                          </div>
                          <div className="text-muted" style={{ fontSize: 12 }}>
                            TG ID: {tx.user_id}
                          </div>
                        </div>
                      </div>

                      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13, marginBottom: 8 }}>
                        <div>
                          <span className="text-muted">Amount: </span>
                          <strong style={{ color: '#ff6464', fontSize: 16 }}>
                            {(tx.amount || 0).toLocaleString()} VC
                          </strong>
                          <span className="text-muted" style={{ marginLeft: 4 }}>
                            (= ₹{(tx.amount || 0).toLocaleString()})
                          </span>
                        </div>
                        <div>
                          <span className="text-muted">Time: </span>
                          <span>{tx.at?.slice(0, 16).replace('T', ' ')}</span>
                        </div>
                      </div>

                      {data.address && (
                        <div style={{
                          padding: '8px 12px', background: 'var(--surface)',
                          borderRadius: 8, fontSize: 13, border: '1px solid var(--border)',
                          marginBottom: 6,
                        }}>
                          <span className="text-muted">💳 Pay to UPI: </span>
                          <strong className="font-mono" style={{ color: 'var(--accent3)' }}>
                            {data.address}
                          </strong>
                        </div>
                      )}

                      <div className="font-mono" style={{ fontSize: 11, color: 'var(--muted)' }}>
                        TX: {tx.id?.slice(0, 16)}...
                      </div>

                      {rejectingWdId === tx.id && (
                        <RejectReasonForm
                          label="Rejecting withdrawal (VC will be refunded)"
                          onConfirm={(reason) => rejectWd.mutate({ id: tx.id, reason })}
                          onCancel={() => setRejectingWdId(null)}
                          isPending={rejectWd.isPending}
                        />
                      )}
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 160 }}>
                      <div style={{
                        fontSize: 11, color: 'var(--accent3)', textAlign: 'center',
                        padding: '6px 10px', background: 'rgba(0,255,255,0.06)',
                        borderRadius: 8, border: '1px solid rgba(0,255,255,0.1)',
                      }}>
                        ⬆️ Pay via PhonePe/GPay first,<br />then mark as paid
                      </div>
                      <button
                        className="btn btn-primary"
                        onClick={() => paidWd.mutate(tx.id)}
                        disabled={paidWd.isPending}
                      >
                        ✅ Mark as Paid
                      </button>
                      {rejectingWdId === tx.id ? null : (
                        <button
                          className="btn btn-danger"
                          onClick={() => setRejectingWdId(tx.id)}
                        >
                          ❌ Reject & Refund
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {tab === 'vouches' && (
        <div>
          {vLoad ? (
            <div className="loading"><div className="spinner" /></div>
          ) : vouches.length === 0 ? (
            <div className="card">
              <div className="empty">
                <div className="empty-icon">🤝</div>
                <div className="empty-text">No pending vouches</div>
                <div className="text-muted" style={{ marginTop: 6, fontSize: 13 }}>
                  When users vouch for each other, pending vouches appear here for review
                </div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {vouches.map(v => {
                const proofs = (() => {
                  try { return JSON.parse(v.proof_json || '[]') } catch { return [] }
                })()
                return (
                  <div key={v.id} className="card">
                    <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16 }}>
                      <div style={{ flex: 1, minWidth: 240 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                          <div style={{
                            width: 40, height: 40, borderRadius: '50%',
                            background: 'rgba(0,170,255,0.1)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
                          }}>🤝</div>
                          <div>
                            <div style={{ fontWeight: 700, fontSize: 15 }}>
                              <span style={{ color: 'var(--accent)' }}>@{v.from_un || `#${v.from_id}`}</span>
                              <span style={{ color: 'var(--muted)', margin: '0 6px' }}>vouches for</span>
                              <span style={{ color: 'var(--accent3)' }}>@{v.to_un || `#${v.to_id}`}</span>
                            </div>
                            <div className="text-muted" style={{ fontSize: 12 }}>
                              {v.at?.slice(0, 16).replace('T', ' ')}
                            </div>
                          </div>
                          <span style={{
                            padding: '2px 10px', borderRadius: 20, fontSize: 11, fontWeight: 600,
                            background: 'rgba(255,170,0,0.15)', color: '#ffaa00',
                          }}>PENDING</span>
                        </div>

                        <div style={{
                          padding: '10px 14px', background: 'var(--surface)',
                          borderRadius: 8, border: '1px solid var(--border)',
                          fontSize: 13, marginBottom: 10, lineHeight: 1.5,
                        }}>
                          📝 {v.text}
                        </div>

                        {proofs.length > 0 ? (
                          <div style={{
                            padding: '8px 12px', background: 'rgba(0,200,255,0.06)',
                            borderRadius: 8, border: '1px solid rgba(0,200,255,0.15)',
                            fontSize: 12,
                          }}>
                            <div style={{ color: '#00c8ff', fontWeight: 600, marginBottom: 4 }}>
                              📸 {proofs.length} proof screenshot(s) submitted
                            </div>
                            <div className="text-muted">
                              Screenshots were forwarded to your Telegram admin DM
                            </div>
                          </div>
                        ) : (
                          <div style={{
                            padding: '6px 12px', background: 'rgba(255,170,0,0.06)',
                            borderRadius: 8, fontSize: 12, color: 'var(--muted)',
                          }}>
                            ⚠️ No proof screenshots submitted
                          </div>
                        )}

                        {rejectingVouchId === v.id && (
                          <div style={{ marginTop: 10 }}>
                            <RejectReasonForm
                              label="Rejecting vouch"
                              onConfirm={(reason) => rejectVouch.mutate({ id: v.id, reason })}
                              onCancel={() => setRejectingVouchId(null)}
                              isPending={rejectVouch.isPending}
                            />
                          </div>
                        )}
                      </div>

                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 140 }}>
                        <button
                          className="btn btn-primary"
                          onClick={() => approveVouch.mutate(v.id)}
                          disabled={approveVouch.isPending}
                        >
                          ✅ Approve Vouch
                        </button>
                        {rejectingVouchId === v.id ? null : (
                          <button
                            className="btn btn-danger"
                            onClick={() => setRejectingVouchId(v.id)}
                          >
                            ❌ Reject
                          </button>
                        )}
                        <div style={{
                          fontSize: 11, color: 'var(--muted)', textAlign: 'center',
                          padding: '4px 8px', borderRadius: 6,
                          background: 'rgba(255,255,255,0.04)',
                        }}>
                          ID: {v.id?.slice(0, 8)}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
