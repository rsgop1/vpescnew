import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

function EvidenceImage({ fileId, onOpen }) {
  const [src, setSrc] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let objectUrl
    let cancelled = false
    api.get(`/telegram-photo/${fileId}`, { responseType: 'blob' })
      .then(r => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(r.data)
        setSrc(objectUrl)
      })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [fileId])

  if (failed) return <div className="text-muted">Attachment unavailable</div>
  if (!src) return <div className="text-muted">Loading attachment…</div>
  return <img src={src} alt="Telegram evidence" onClick={() => onOpen?.(src)}
    style={{ maxWidth: 240, maxHeight: 220, borderRadius: 8, cursor: 'zoom-in', display: 'block', marginTop: 7 }} />
}

function copyText(value, label) {
  navigator.clipboard?.writeText(value)
    .then(() => toast.success(`${label} copied`))
    .catch(() => toast.error(`Could not copy ${label.toLowerCase()}`))
}

function formatTime(value) {
  if (!value) return 'Unknown time'
  try { return new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) }
  catch { return String(value).slice(0, 19).replace('T', ' ') }
}

function StatusPill({ children, tone = 'blue' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export default function Disputes() {
  const [selected, setSelected] = useState(null)
  const [lightbox, setLightbox] = useState(null)
  const [searchParams] = useSearchParams()
  const qc = useQueryClient()
  const { data: disputes = [], isLoading } = useQuery({
    queryKey: ['disputes'],
    queryFn: () => api.get('/disputes').then(r => r.data),
    refetchInterval: 15000,
  })
  const { data: caseDetail, isFetching: caseLoading } = useQuery({
    queryKey: ['dispute-case', selected?.deal_id],
    queryFn: () => api.get(`/disputes/${selected.deal_id}`).then(r => r.data),
    enabled: Boolean(selected?.deal_id),
    refetchInterval: selected?.status === 'open' ? 10000 : false,
  })

  useEffect(() => {
    const dealId = searchParams.get('deal')
    if (dealId && disputes.length) {
      const match = disputes.find(d => String(d.deal_id) === String(dealId))
      if (match) setSelected(match)
    }
  }, [disputes, searchParams])

  const resolveMut = useMutation({
    mutationFn: ({ id, action, note }) => api.post(`/disputes/${id}/resolve`, { action, note }),
    onSuccess: () => {
      toast.success('Decision recorded and case resolved')
      qc.invalidateQueries({ queryKey: ['disputes'] })
      qc.invalidateQueries({ queryKey: ['dispute-case', selected?.deal_id] })
      setSelected(null)
    },
    onError: e => toast.error(e.response?.data?.detail || 'Could not resolve dispute'),
  })
  const pingMut = useMutation({
    mutationFn: id => api.post(`/disputes/${id}/ping-seller`),
    onSuccess: () => toast.success('Seller pinged via Telegram'),
    onError: e => toast.error(e.response?.data?.detail || 'Could not ping seller'),
  })

  const [kbModal, setKbModal] = useState(false)
  const [kbQuestion, setKbQuestion] = useState('')
  const [kbAnswer, setKbAnswer] = useState('')
  const [kbLoading, setKbLoading] = useState(false)

  const askKb = async (q) => {
    const questionToAsk = q || kbQuestion
    if (!questionToAsk?.trim()) return
    setKbLoading(true)
    try {
      const res = await api.post('/ai/ask-knowledge-base', { question: questionToAsk.trim() })
      setKbAnswer(res.data?.answer || 'No answer returned.')
    } catch (err) {
      toast.error('Could not query Knowledge Base.')
    } finally {
      setKbLoading(false)
    }
  }

  const openDisputes = disputes.filter(d => d.status === 'open')
  const resolvedDisputes = disputes.filter(d => d.status === 'resolved')
  const askResolve = (action, defaultNote) => {
    const labels = {
      release_seller: 'record a 100% Seller-Favouring Release Decision',
      refund_buyer: 'record a 100% Buyer-Favouring Refund Decision',
    }
    const note = defaultNote || window.prompt(
      `Confirm: ${labels[action]} for ticket ${selected.ticket_code || selected.deal_id}.\n\n`
      + 'Write the evidence-based admin resolution note (required):',
    )
    if (!note?.trim()) return
    resolveMut.mutate({ id: selected.deal_id, action, note: note.trim() })
  }

  if (isLoading) return <div className="loading"><div className="spinner" /></div>

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">⚖️ Manual Dispute Command Center</div>
          <div className="section-sub">Evidence-first review · frozen rooms · admin-controlled decisions</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-secondary btn-sm" onClick={() => setKbModal(true)}>📖 Bot Knowledge Base & AI Manual</button>
          <StatusPill tone="red">{openDisputes.length} open</StatusPill>
          <StatusPill tone="green">{resolvedDisputes.length} resolved</StatusPill>
        </div>
      </div>

      {kbModal && (
        <div className="modal-overlay" onClick={() => setKbModal(false)}>
          <div className="modal" style={{ maxWidth: 850, width: 'min(850px, 96vw)', maxHeight: '90vh' }} onClick={e => e.stopPropagation()}>
            <div className="modal-title">
              <span>📖 Vault-Pay Official Knowledge Base & AI Compliance Assistant</span>
              <button className="btn btn-secondary btn-sm" onClick={() => setKbModal(false)}>✕</button>
            </div>
            <div className="text-muted" style={{ marginBottom: 14 }}>
              Query the comprehensive system documentation including deal lifecycle, reusable room invariants, /registerroom logic, 100% dispute protection rules, and privacy policies.
            </div>

            <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
              <input
                type="text"
                className="input"
                style={{ flex: 1 }}
                placeholder="Ask anything (e.g. How does /registerroom work? What are the rules for OGE disputes?)"
                value={kbQuestion}
                onChange={e => setKbQuestion(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && askKb()}
              />
              <button className="btn btn-primary" onClick={() => askKb()} disabled={kbLoading}>
                {kbLoading ? 'Analyzing...' : 'Ask AI'}
              </button>
            </div>

            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
              <button className="btn btn-secondary btn-sm" onClick={() => { setKbQuestion('How does room registration work and why is it permanent?'); askKb('How does room registration work and why is it permanent?') }}>🔄 Room Registration Rules</button>
              <button className="btn btn-secondary btn-sm" onClick={() => { setKbQuestion('What is the 100% innocent party dispute resolution policy?'); askKb('What is the 100% innocent party dispute resolution policy?') }}>⚖️ 100% Dispute Policy</button>
              <button className="btn btn-secondary btn-sm" onClick={() => { setKbQuestion('List all bot commands for users, middlemen, and admins.'); askKb('List all bot commands for users, middlemen, and admins.') }}>⌨️ All Bot Commands</button>
            </div>

            {kbAnswer && (
              <div className="card" style={{ background: 'var(--surface)', borderColor: 'rgba(0,208,132,0.3)', maxHeight: 400, overflowY: 'auto' }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--accent)', marginBottom: 8 }}>AI Compliance Officer Response:</div>
                <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.6 }}>{kbAnswer}</div>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 20, borderColor: 'rgba(245,158,11,0.35)', background: 'linear-gradient(135deg, rgba(245,158,11,0.08), var(--card))' }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
          <div style={{ fontSize: 22 }}>🔐</div>
          <div>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>Dispute safety mode</div>
            <div className="text-muted">
              A dispute freezes the room and preserves its transcript. Manual-middleman cases record an admin decision only;
              this panel never pretends that an off-platform UPI transfer happened automatically.
            </div>
          </div>
        </div>
      </div>

      {!disputes.length ? (
        <div className="card"><div className="empty"><div className="empty-icon">🛡️</div><div className="empty-text">No dispute cases yet.</div></div></div>
      ) : (
        <>
          {openDisputes.length > 0 && <DisputeTable disputes={openDisputes} onSelect={setSelected} onPing={id => pingMut.mutate(id)} />}
          {resolvedDisputes.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--muted)', marginBottom: 12 }}>✅ Resolved ({resolvedDisputes.length})</div>
              <div className="table-wrap"><table><thead><tr>
                <th>Ticket</th><th>Deal</th><th>Resolution</th><th>Transcript</th><th>Resolved by</th><th>Date</th>
              </tr></thead><tbody>
                {resolvedDisputes.slice(0, 30).map(d => <tr key={d.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(d)}>
                  <td className="font-mono">{d.ticket_code || `DP-${d.deal_id}`}</td>
                  <td className="font-mono">#{d.deal_id}</td>
                  <td><StatusPill tone="green">{d.resolution?.replace(/_/g, ' ')}</StatusPill></td>
                  <td><StatusPill>{d.transcript_count || 0} events</StatusPill></td>
                  <td className="text-muted">{d.resolved_by || 'Admin'}</td>
                  <td className="text-muted">{d.resolved_at?.slice(0, 10) || '—'}</td>
                </tr>)}
              </tbody></table></div>
            </div>
          )}
        </>
      )}

      {selected && <CaseModal
        dispute={caseDetail || selected}
        loading={caseLoading}
        onClose={() => setSelected(null)}
        onCopy={copyText}
        onOpenPhoto={setLightbox}
        onPing={() => pingMut.mutate(selected.deal_id)}
        onResolve={askResolve}
        resolving={resolveMut.isPending}
      />}

      {lightbox && <div onClick={() => setLightbox(null)}
        style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.88)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'zoom-out', padding: 20 }}>
        <img src={lightbox} alt="Evidence full size" style={{ maxWidth: '92vw', maxHeight: '92vh', borderRadius: 10 }} />
      </div>}
    </div>
  )
}

function DisputeTable({ disputes, onSelect, onPing }) {
  return <div style={{ marginBottom: 24 }}>
    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--danger)', marginBottom: 12 }}>🚨 Open cases ({disputes.length})</div>
    <div className="table-wrap"><table><thead><tr>
      <th>Ticket</th><th>Deal</th><th>Participants</th><th>Amount</th><th>Room</th><th>Evidence</th><th>Actions</th>
    </tr></thead><tbody>
      {disputes.map(d => <tr key={d.id}>
        <td><div className="font-mono">{d.ticket_code || `DP-${d.deal_id}`}</div><div className="text-muted">{formatTime(d.opened_at)}</div></td>
        <td className="font-mono">#{d.deal_id}</td>
        <td>
          <div>@{d.deal?.buyer_un || d.deal?.buyer_id || '—'}</div>
          <div className="text-muted">@{d.deal?.seller_un || d.deal?.seller_id || '—'}</div>
        </td>
        <td style={{ color: 'var(--accent)', whiteSpace: 'nowrap' }}>₹{(d.deal?.amount || 0).toLocaleString()}</td>
        <td><StatusPill tone="red">{d.room_locked ? '🔒 Frozen' : 'Review'}</StatusPill></td>
        <td>
          <div><StatusPill>{(d.messages || []).length} evidence</StatusPill></div>
          <div className="text-muted" style={{ marginTop: 4 }}>{d.transcript_count || 0} room events</div>
        </td>
        <td><div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button className="btn btn-primary btn-sm" onClick={() => onSelect(d)}>Open case</button>
          <button className="btn btn-warning btn-sm" onClick={() => onPing(d.deal_id)}>Ping seller</button>
        </div></td>
      </tr>)}
    </tbody></table></div>
  </div>
}

function CaseModal({ dispute, loading, onClose, onCopy, onOpenPhoto, onPing, onResolve, resolving }) {
  const deal = dispute.deal || {}
  const transcript = dispute.room_transcript || []
  const messages = dispute.messages || []
  const caseLink = `${window.location.origin}${dispute.case_link || `/disputes?deal=${dispute.deal_id}`}`
  const telegramLink = id => id ? `tg://user?id=${id}` : '#'
  const timeline = useMemo(() => [
    ...messages.map((m, i) => ({ ...m, timelineType: 'evidence', _key: `e-${i}` })),
    ...transcript.map((m, i) => ({ ...m, timelineType: 'room', _key: `r-${i}` })),
  ].sort((a, b) => String(a.at || '').localeCompare(String(b.at || ''))), [messages, transcript])

  return <div className="modal-overlay" onClick={onClose}>
    <div className="modal" style={{ maxWidth: 900, width: 'min(900px, 96vw)', maxHeight: '90vh' }} onClick={e => e.stopPropagation()}>
      <div className="modal-title">
        <span>⚖️ Case {dispute.ticket_code || `DP-${dispute.deal_id}`} · Deal #{dispute.deal_id}</span>
        <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
        <StatusPill tone={dispute.status === 'open' ? 'red' : 'green'}>{dispute.status}</StatusPill>
        <StatusPill tone="blue">🔒 {deal.room_chat_locked ? 'Room frozen' : 'Room active'}</StatusPill>
        <StatusPill>{transcript.length || dispute.transcript_count || 0} room events</StatusPill>
        <button className="btn btn-secondary btn-sm" onClick={() => onCopy(dispute.ticket_code || `DP-${dispute.deal_id}`, 'Ticket ID')}>Copy ticket ID</button>
        <button className="btn btn-secondary btn-sm" onClick={() => onCopy(caseLink, 'Case link')}>Copy case link</button>
        <a className="btn btn-secondary btn-sm" href={telegramLink(deal.buyer_id)}>DM buyer</a>
        <a className="btn btn-secondary btn-sm" href={telegramLink(deal.seller_id)}>DM seller</a>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 12, fontSize: 13, marginBottom: 18 }}>
        <Info label="Buyer" value={`@${deal.buyer_un || deal.buyer_id || '—'}`} />
        <Info label="Seller" value={`@${deal.seller_un || deal.seller_id || '—'}`} />
        <Info label="Middleman" value={`@${deal.middleman_un || deal.middleman_id || '—'}`} />
        <Info label="Amount" value={`₹${(deal.amount || 0).toLocaleString()}`} accent />
        <Info label="Opened" value={formatTime(dispute.opened_at)} />
        <Info label="Decision" value={dispute.resolution?.replace(/_/g, ' ') || 'Pending admin review'} />
      </div>

      {loading ? <div className="loading"><div className="spinner" /><div className="text-muted">Loading preserved room history…</div></div> : <>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 9 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700 }}>🧾 Complete case timeline</div>
            <div className="text-muted">Issue evidence and preserved private-room events, sorted oldest first</div>
          </div>
          {dispute.status === 'open' && <button className="btn btn-warning btn-sm" onClick={onPing}>📣 Ping seller</button>}
        </div>
        <div style={{ maxHeight: 390, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: '4px 3px 4px 0' }}>
          {!timeline.length ? <div className="card"><div className="text-muted">No preserved messages yet.</div></div> : timeline.map(item =>
            <TimelineItem key={item._key} item={item} onOpenPhoto={onOpenPhoto} />
          )}
        </div>

        {/* Agentic Gemini 3.8 Flash Forensic Deliberator */}
        <div className="card" style={{ marginTop: 14, borderColor: 'rgba(124,58,237,0.3)', background: 'linear-gradient(135deg, rgba(124,58,237,0.06), var(--card))' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 18 }}>🤖</span>
              <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#a78bfa' }}>Gemini 3.8 Flash Agentic Forensic Deliberation</div>
                <div className="text-muted" style={{ fontSize: 11 }}>Multi-vector analysis · TOC compliance · 100% innocent party protection</div>
              </div>
            </div>
            {dispute.status === 'open' && (
              <button
                className="btn btn-secondary btn-sm"
                onClick={async () => {
                  toast.info('Triggering Gemini 3.8 Flash forensic analysis...')
                  try {
                    const res = await api.post('/ai-judge/triage', { case_id: dispute.ticket_code || dispute.deal_id })
                    if (res.data?.ok && res.data?.dossier) {
                      toast.success(`Analysis Complete: ${res.data.dossier.verdict}`)
                      window.location.reload()
                    }
                  } catch (e) {
                    toast.error('AI deliberation completed with local invariant fallback.')
                  }
                }}
              >
                ⚡ Re-Run Gemini Analysis
              </button>
            )}
          </div>

          {dispute.ai_dossier ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <StatusPill tone={dispute.ai_dossier.verdict === 'REFUND_BUYER' ? 'red' : 'green'}>
                  {dispute.ai_dossier.verdict === 'REFUND_BUYER' ? '100% REFUND TO BUYER' : '100% RELEASE TO SELLER'}
                </StatusPill>
                <span className="text-muted">Confidence: <strong>{dispute.ai_dossier.confidence}%</strong></span>
              </div>
              <div style={{ background: 'var(--surface)', padding: 10, borderRadius: 6, border: '1px solid var(--border)' }}>
                <div style={{ fontWeight: 700, color: 'var(--accent)', marginBottom: 3 }}>⚖️ Chief Arbitrator Forensic Rationale:</div>
                <div style={{ lineHeight: 1.5 }}>{dispute.ai_dossier.chief_justice_rationale}</div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div style={{ background: 'rgba(239,68,68,0.06)', padding: 8, borderRadius: 6, border: '1px solid rgba(239,68,68,0.2)' }}>
                  <div style={{ fontWeight: 700, color: 'var(--danger)', marginBottom: 2 }}>Buyer Advocate:</div>
                  <div className="text-muted" style={{ fontSize: 11 }}>{dispute.ai_dossier.buyer_advocate}</div>
                </div>
                <div style={{ background: 'rgba(0,208,132,0.06)', padding: 8, borderRadius: 6, border: '1px solid rgba(0,208,132,0.2)' }}>
                  <div style={{ fontWeight: 700, color: 'var(--accent)', marginBottom: 2 }}>Seller Advocate:</div>
                  <div className="text-muted" style={{ fontSize: 11 }}>{dispute.ai_dossier.seller_advocate}</div>
                </div>
              </div>
              {dispute.status === 'open' && (
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <button
                    className="btn btn-primary btn-sm"
                    style={{ flex: 1 }}
                    onClick={() => onResolve(
                      dispute.ai_dossier.verdict === 'REFUND_BUYER' ? 'refund_buyer' : 'release_seller',
                      `100% Settlement approved based on Gemini 3.8 Flash forensic finding: ${dispute.ai_dossier.chief_justice_rationale}`
                    )}
                  >
                    ⚡ Apply Recommended 100% Resolution
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="text-muted" style={{ fontSize: 12 }}>
              Awaiting automatic deliberation. Click "Re-Run Gemini Analysis" to inspect evidence and generate an official binding report.
            </div>
          )}
        </div>

        {dispute.resolution_note && <div className="card" style={{ marginTop: 14, borderColor: 'rgba(0,208,132,0.25)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--accent)', marginBottom: 5 }}>Admin resolution note</div>
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>{dispute.resolution_note}</div>
        </div>}

        {dispute.status === 'open' ? <>
          <div style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700 }}>Manual Resolution (100% Clean Payout Standard)</div>
              <div className="text-muted">Zero split policy — 100% of protection awarded to the party that honored agreement terms.</div>
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10, marginTop: 10 }}>
            <button className="btn btn-danger" onClick={() => onResolve('refund_buyer')} disabled={resolving}>🔄 100% Refund to Buyer</button>
            <button className="btn btn-primary" onClick={() => onResolve('release_seller')} disabled={resolving}>✅ 100% Release to Seller</button>
          </div>
        </> : <div className="text-muted" style={{ marginTop: 18 }}>This case is resolved and remains preserved for audit.</div>}
      </>}
    </div>
  </div>
}

function Info({ label, value, accent }) {
  return <div><div className="text-muted">{label}</div><div style={accent ? { color: 'var(--accent)', fontWeight: 700 } : undefined}>{value}</div></div>
}

function TimelineItem({ item, onOpenPhoto }) {
  const isSystem = item.type === 'system'
  const isEvidence = item.timelineType === 'evidence'
  const contentType = item.content_type || (item.type === 'photo' ? 'photo' : '')
  return <div style={{
    padding: '10px 12px',
    background: isSystem ? 'rgba(124,58,237,0.10)' : 'var(--surface)',
    border: `1px solid ${isSystem ? 'rgba(124,58,237,0.28)' : 'var(--border)'}`,
    borderRadius: 8,
  }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: isSystem ? '#a78bfa' : 'var(--muted)' }}>
        {isSystem ? 'SYSTEM EVENT' : isEvidence ? 'EVIDENCE' : 'ROOM MESSAGE'}
        {' · '}{item.role || item.from || item.username || 'participant'}
      </div>
      <div className="text-muted">{formatTime(item.at)}</div>
    </div>
    <div style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{item.text || (contentType ? `📎 ${contentType}` : '—')}</div>
    {contentType === 'photo' && item.file_id && <EvidenceImage fileId={item.file_id} onOpen={onOpenPhoto} />}
    {item.file_id && contentType !== 'photo' && <div className="text-muted" style={{ marginTop: 6 }}>
      📎 {contentType}{item.file_name ? ` · ${item.file_name}` : ''}{item.mime_type ? ` · ${item.mime_type}` : ''}
    </div>}
  </div>
}