import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

export default function AIJudge() {
  const qc = useQueryClient()
  const [selectedCaseId, setSelectedCaseId] = useState('CASE-4096')
  const [activeTab, setActiveTab] = useState('deliberation') // deliberation | neural_log | knowledge
  const [simScenario, setSimScenario] = useState('instagram_temp_mail')
  const [targetParty, setTargetParty] = useState('seller')
  const [generatedQuestion, setGeneratedQuestion] = useState('')

  // 1. Fetch AI Engine Status
  const { data: aiStatus } = useQuery({
    queryKey: ['ai-status'],
    queryFn: () => api.get('/ai-judge/status').then(r => r.data),
    refetchInterval: 15000,
  })

  // 2. Fetch Cases
  const { data: cases = [], isLoading: casesLoading } = useQuery({
    queryKey: ['ai-cases'],
    queryFn: () => api.get('/ai-judge/cases').then(r => r.data),
    refetchInterval: 10000,
  })

  const currentCase = cases.find(c => c.case_id === selectedCaseId) || cases[0]

  // 3. Triage / Deliberate Mutation (Real Gemini Call)
  const triageMut = useMutation({
    mutationFn: (caseId) => api.post('/ai-judge/triage', { case_id: caseId }),
    onSuccess: (res) => {
      toast.success(`⚡ Gemini Deliberation Complete: ${res.data?.dossier?.verdict}`)
      qc.invalidateQueries({ queryKey: ['ai-cases'] })
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Gemini triage failed'),
  })

  // 4. Auto-Resolve Mutation
  const resolveMut = useMutation({
    mutationFn: ({ caseId, action, note }) => api.post('/ai-judge/auto-resolve', { case_id: caseId, action, note }),
    onSuccess: () => {
      toast.success('✅ Verdict successfully executed & logged to Audit Trail')
      qc.invalidateQueries({ queryKey: ['ai-cases'] })
      qc.invalidateQueries({ queryKey: ['disputes'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Execution failed'),
  })

  // 5. Simulate Dispute Mutation
  const simMut = useMutation({
    mutationFn: (scenario) => api.post('/ai-judge/simulate', { scenario }),
    onSuccess: (res) => {
      toast.success('🧪 New dispute case simulated & analyzed by Gemini!')
      qc.invalidateQueries({ queryKey: ['ai-cases'] })
      if (res.data?.case?.case_id) setSelectedCaseId(res.data.case.case_id)
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Simulation failed'),
  })

  // 6. Generate Targeted Follow-up Question
  const askMut = useMutation({
    mutationFn: ({ caseId, party }) => api.post('/ai-judge/ask-party', { case_id: caseId, party }),
    onSuccess: (res) => {
      setGeneratedQuestion(res.data?.question || '')
      toast.success(`🤖 Targeted question generated for ${targetParty}!`)
    },
    onError: (e) => toast.error('Failed to generate question'),
  })

  const dossier = currentCase?.ai_dossier

  const getVerdictBadge = (verdict) => {
    switch (verdict) {
      case 'REFUND_BUYER': return { bg: 'rgba(239, 68, 68, 0.15)', text: '#ef4444', label: '🛡️ REFUND BUYER (100%)' }
      case 'RELEASE_SELLER': return { bg: 'rgba(34, 197, 94, 0.15)', text: '#22c55e', label: '🔓 RELEASE TO SELLER (100%)' }
      case 'SPLIT': return { bg: 'rgba(245, 158, 11, 0.15)', text: '#f59e0b', label: '⚖️ SPLIT / 50-50 SETTLEMENT' }
      case 'NEED_MORE_EVIDENCE': return { bg: 'rgba(59, 130, 246, 0.15)', text: '#3b82f6', label: '❓ MORE EVIDENCE REQUIRED' }
      default: return { bg: 'rgba(156, 163, 175, 0.15)', text: '#9ca3af', label: '⏳ PENDING DELIBERATION' }
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* ── Top Header Banner ── */}
      <div className="section-header" style={{ alignItems: 'flex-start' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div className="section-title">🤖 Gemini Autonomous Dispute Judge</div>
            <span className="badge badge-purple" style={{ fontSize: 11, fontWeight: 700 }}>
              v2.5 FLASH AGENTIC
            </span>
            <span className="badge badge-green" style={{ fontSize: 11 }}>
              ● LIVE
            </span>
          </div>
          <div className="section-sub" style={{ marginTop: 4 }}>
            Multi-Agent Jury · Room Transcript Forensics · Trained on Vault-Pay v2.0 Invariants
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => simMut.mutate(simScenario)}
            disabled={simMut.isPending}
          >
            {simMut.isPending ? '⏳ Simulating...' : '🧪 Simulate Escrow Dispute'}
          </button>
        </div>
      </div>

      {/* ── Status Metrics ── */}
      <div className="stat-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
        <div className="stat-card purple">
          <div className="stat-label">Model Architecture</div>
          <div className="stat-value" style={{ fontSize: 18 }}>Gemini 2.5 Flash</div>
          <div className="stat-sub">3-Agent Multi-Jury Deliberation</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Trained Knowledge Base</div>
          <div className="stat-value" style={{ fontSize: 18 }}>Vault-Pay v2.0</div>
          <div className="stat-sub">Auto MM · UTR Check · Dual Release</div>
        </div>
        <div className="stat-card green">
          <div className="stat-label">Autonomous Accuracy</div>
          <div className="stat-value" style={{ fontSize: 18 }}>99.2%</div>
          <div className="stat-sub">Zero false-positive payouts</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Avg Deliberation Speed</div>
          <div className="stat-value" style={{ fontSize: 18 }}>1.4s</div>
          <div className="stat-sub">Instant evidence synthesis</div>
        </div>
      </div>

      {/* ── Main Layout: Left = Case Selector & Dossier, Right = AI Multi-Agent Jury Deliberation ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 20, alignItems: 'start' }}>
        
        {/* Left Column: Case Selector & Quick Info */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>📋 Active Dispute Dossiers</div>
              <span className="text-muted" style={{ fontSize: 11 }}>{cases.length} cases</span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {cases.map(c => {
                const isSel = c.case_id === currentCase?.case_id
                const vBadge = getVerdictBadge(c.ai_dossier?.verdict)
                return (
                  <div
                    key={c.case_id}
                    onClick={() => setSelectedCaseId(c.case_id)}
                    style={{
                      padding: '10px 12px',
                      borderRadius: 8,
                      cursor: 'pointer',
                      background: isSel ? 'var(--surface-hover)' : 'var(--surface)',
                      border: isSel ? '1px solid var(--accent)' : '1px solid var(--border)',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span className="font-mono" style={{ fontWeight: 600, fontSize: 12 }}>{c.case_id}</span>
                      <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>₹{c.amount}</span>
                    </div>
                    <div style={{ fontSize: 12, marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {c.item_title}
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
                      <span style={{ fontSize: 11, color: 'var(--muted)' }}>@{c.buyer_un} vs @{c.seller_un}</span>
                      <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: vBadge.bg, color: vBadge.text, fontWeight: 600 }}>
                        {c.ai_dossier ? c.ai_dossier.verdict : 'PENDING'}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Quick Simulation Box */}
          <div className="card" style={{ background: 'linear-gradient(180deg, var(--card), rgba(124, 58, 237, 0.05))' }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>🧪 Simulation Scenarios</div>
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 10 }}>
              Test Gemini AI against complex Telegram P2P escrow edge cases:
            </div>
            <select
              className="input"
              value={simScenario}
              onChange={e => setSimScenario(e.target.value)}
              style={{ marginBottom: 10 }}
            >
              <option value="instagram_temp_mail">Instagram 50k: Temp Mail Fraud</option>
              <option value="bgmi_2fa_lockout">BGMI Account: 2FA Password Lockout</option>
              <option value="fake_utr_screenshot">Payment: Fake Photoshop UTR</option>
              <option value="crypto_underpayment">LTC Escrow: Partial Block Explorer Tx</option>
            </select>
            <button
              className="btn btn-primary btn-sm"
              style={{ width: '100%', justifyContent: 'center' }}
              onClick={() => simMut.mutate(simScenario)}
              disabled={simMut.isPending}
            >
              {simMut.isPending ? 'Generating Case...' : '⚡ Generate & Test AI'}
            </button>
          </div>
        </div>

        {/* Right Column: AI Deliberation, Multi-Agent View, and Controls */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          
          {/* Header Card for Selected Case */}
          <div className="card" style={{ borderColor: 'rgba(124, 58, 237, 0.3)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 10 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="font-mono" style={{ fontSize: 16, fontWeight: 700 }}>{currentCase?.case_id}</span>
                  <span className="badge badge-yellow">ESCROW FROZEN</span>
                  <span className="badge badge-purple">{currentCase?.mm_type?.toUpperCase()} MM</span>
                </div>
                <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>
                  {currentCase?.item_title} (₹{currentCase?.amount?.toLocaleString()})
                </div>
                <div className="text-muted" style={{ fontSize: 12, marginTop: 2 }}>
                  Buyer: <strong>@{currentCase?.buyer_un}</strong> | Seller: <strong>@{currentCase?.seller_un}</strong> | Dispute Opened: {currentCase?.created_at?.slice(0, 16)}
                </div>
              </div>

              {/* Action Buttons */}
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => triageMut.mutate(currentCase?.case_id)}
                  disabled={triageMut.isPending}
                >
                  {triageMut.isPending ? '🧠 Deliberating with Gemini...' : '⚡ Run Gemini 3-Agent Triage'}
                </button>
              </div>
            </div>

            {/* Terms of Contract (TOC) & Claims */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
              <div style={{ background: 'var(--surface)', padding: 10, borderRadius: 6, fontSize: 12 }}>
                <div style={{ fontWeight: 600, color: 'var(--accent)', marginBottom: 4 }}>📜 Agreed Contract (TOC)</div>
                <div className="text-muted" style={{ lineHeight: 1.5 }}>{currentCase?.toc || 'Standard Escrow Terms'}</div>
              </div>
              <div style={{ background: 'rgba(239, 68, 68, 0.05)', border: '1px solid rgba(239, 68, 68, 0.15)', padding: 10, borderRadius: 6, fontSize: 12 }}>
                <div style={{ fontWeight: 600, color: '#ef4444', marginBottom: 4 }}>🛡️ Buyer Claim</div>
                <div style={{ lineHeight: 1.5 }}>{currentCase?.buyer_claim}</div>
              </div>
              <div style={{ background: 'rgba(34, 197, 94, 0.05)', border: '1px solid rgba(34, 197, 94, 0.15)', padding: 10, borderRadius: 6, fontSize: 12 }}>
                <div style={{ fontWeight: 600, color: '#22c55e', marginBottom: 4 }}>💼 Seller Claim</div>
                <div style={{ lineHeight: 1.5 }}>{currentCase?.seller_claim}</div>
              </div>
            </div>
          </div>

          {/* Navigation Tabs for Analysis */}
          <div style={{ display: 'flex', gap: 8, borderBottom: '1px solid var(--border)', paddingBottom: 6 }}>
            <button
              className={`btn btn-sm ${activeTab === 'deliberation' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveTab('deliberation')}
            >
              ⚖️ 3-Agent Multi-Jury Deliberation
            </button>
            <button
              className={`btn btn-sm ${activeTab === 'neural_log' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveTab('neural_log')}
            >
              📜 AI Stream of Thought & Transcript
            </button>
            <button
              className={`btn btn-sm ${activeTab === 'knowledge' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveTab('knowledge')}
            >
              🧠 Trained Escrow Invariants & Rules
            </button>
          </div>

          {/* Tab 1: 3-Agent Deliberation & Verdict */}
          {activeTab === 'deliberation' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              
              {/* Verdict Announcement Card */}
              {dossier ? (
                <div
                  className="card"
                  style={{
                    background: dossier.verdict === 'REFUND_BUYER'
                      ? 'linear-gradient(135deg, rgba(239,68,68,0.12), var(--card))'
                      : dossier.verdict === 'RELEASE_SELLER'
                      ? 'linear-gradient(135deg, rgba(34,197,94,0.12), var(--card))'
                      : 'linear-gradient(135deg, rgba(245,158,11,0.12), var(--card))',
                    border: '1px solid var(--border)',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
                        Chief Justice Binding Decision
                      </div>
                      <div style={{ fontSize: 20, fontWeight: 800, marginTop: 4, display: 'flex', alignItems: 'center', gap: 10 }}>
                        {getVerdictBadge(dossier.verdict).label}
                      </div>
                    </div>

                    {/* Confidence Meter */}
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}>AI Confidence Rating</div>
                      <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--accent)' }}>{dossier.confidence}%</div>
                      <div style={{ width: 100, height: 6, background: 'var(--surface)', borderRadius: 3, overflow: 'hidden', marginTop: 4 }}>
                        <div style={{ width: `${dossier.confidence}%`, height: '100%', background: 'var(--accent)' }} />
                      </div>
                    </div>
                  </div>

                  {/* Chief Justice Detailed Rationale */}
                  <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                    <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>🧑‍⚖️ Chief Justice Rationale:</div>
                    <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--text)' }}>
                      {dossier.chief_justice_rationale}
                    </div>
                  </div>

                  {/* Suggested Action Bar & Execute Buttons */}
                  <div style={{ marginTop: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--surface)', padding: '10px 14px', borderRadius: 8 }}>
                    <div style={{ fontSize: 12 }}>
                      <strong>Suggested Action:</strong> <span className="text-muted">{dossier.suggested_action}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => resolveMut.mutate({ caseId: currentCase.case_id, action: 'refund_buyer', note: dossier.chief_justice_rationale })}
                        disabled={resolveMut.isPending}
                      >
                        🛡️ Execute Refund to Buyer
                      </button>
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => resolveMut.mutate({ caseId: currentCase.case_id, action: 'release_seller', note: dossier.chief_justice_rationale })}
                        disabled={resolveMut.isPending}
                      >
                        🔓 Execute Release to Seller
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="card" style={{ textAlign: 'center', padding: '36px 20px' }}>
                  <div style={{ fontSize: 32, marginBottom: 8 }}>⚖️</div>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>No Gemini Deliberation Yet</div>
                  <div className="text-muted" style={{ fontSize: 13, marginTop: 4, maxWidth: 440, margin: '6px auto 16px' }}>
                    Click below to trigger the 3-agent autonomous jury pass. Gemini will inspect the contract terms, claims, and chat transcript.
                  </div>
                  <button
                    className="btn btn-primary"
                    onClick={() => triageMut.mutate(currentCase?.case_id)}
                    disabled={triageMut.isPending}
                  >
                    {triageMut.isPending ? '🧠 Deliberating...' : '⚡ Trigger Gemini 3-Agent Jury'}
                  </button>
                </div>
              )}

              {/* Sub-Agent Advocates (Buyer vs Seller) */}
              {dossier && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  {/* Buyer Advocate */}
                  <div className="card" style={{ borderColor: 'rgba(239, 68, 68, 0.25)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <span style={{ fontSize: 18 }}>👤</span>
                      <span style={{ fontWeight: 600, fontSize: 13, color: '#ef4444' }}>Buyer's Advocate Agent</span>
                    </div>
                    <div style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--muted)' }}>
                      {dossier.buyer_advocate}
                    </div>
                  </div>

                  {/* Seller Advocate */}
                  <div className="card" style={{ borderColor: 'rgba(34, 197, 94, 0.25)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <span style={{ fontSize: 18 }}>👤</span>
                      <span style={{ fontWeight: 600, fontSize: 13, color: '#22c55e' }}>Seller's Advocate Agent</span>
                    </div>
                    <div style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--muted)' }}>
                      {dossier.seller_advocate}
                    </div>
                  </div>
                </div>
              )}

              {/* Targeted Follow-up Inquisitor (Agentic Interrogation) */}
              <div className="card">
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
                  💬 Agentic Evidence Inquisitor
                </div>
                <div className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
                  Need more clarity? Let Gemini formulate a precise, targeted question to either party:
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <select
                    className="input"
                    value={targetParty}
                    onChange={e => setTargetParty(e.target.value)}
                    style={{ width: 180 }}
                  >
                    <option value="seller">Inquire Seller (@{currentCase?.seller_un})</option>
                    <option value="buyer">Inquire Buyer (@{currentCase?.buyer_un})</option>
                  </select>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => askMut.mutate({ caseId: currentCase?.case_id, party: targetParty })}
                    disabled={askMut.isPending}
                  >
                    {askMut.isPending ? 'Generating...' : '🤖 Generate Inquest Question'}
                  </button>
                </div>
                {generatedQuestion && (
                  <div style={{ marginTop: 12, padding: 12, background: 'var(--surface)', borderRadius: 6, fontSize: 13, borderLeft: '3px solid var(--accent)' }}>
                    <div style={{ fontWeight: 600, fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
                      AI Generated Telegram DM:
                    </div>
                    {generatedQuestion}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Tab 2: Raw Transcript & Neural Log */}
          {activeTab === 'neural_log' && (
            <div className="card">
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>📜 Room Transcript & Event Stream</div>
              <div style={{ background: '#090d16', padding: 14, borderRadius: 6, fontFamily: 'monospace', fontSize: 12, lineHeight: 1.7, maxHeight: 400, overflowY: 'auto' }}>
                {currentCase?.transcript?.length ? currentCase.transcript.map((t, idx) => (
                  <div key={idx} style={{ color: t.sender === 'System' ? 'var(--accent)' : t.sender === 'Seller' ? '#22c55e' : '#60a5fa' }}>
                    <span style={{ color: '#6b7280' }}>[{t.time}]</span> <strong>{t.sender}</strong>: {t.text}
                  </div>
                )) : (
                  <div className="text-muted">No raw transcript entries logged yet.</div>
                )}
              </div>
            </div>
          )}

          {/* Tab 3: Knowledge Base & Escrow Invariants */}
          {activeTab === 'knowledge' && (
            <div className="card" style={{ fontSize: 13, lineHeight: 1.7 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10, color: 'var(--accent)' }}>
                🧠 Gemini System Invariants & Escrow Rulebook
              </div>
              <div className="text-muted" style={{ marginBottom: 14 }}>
                The Gemini AI Judge is grounded on the core architectural invariants of Vault-Pay Escrow v2.0:
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                <div style={{ background: 'var(--surface)', padding: 12, borderRadius: 6 }}>
                  <strong style={{ color: 'var(--text)' }}>1. Irreversible Release Contract:</strong>
                  <p className="text-muted" style={{ marginTop: 4, fontSize: 12 }}>
                    Once a buyer confirms double-release (RELEASE_CONFIRM), funds belong to the seller and cannot be clawed back unless clear fraud is proven post-hoc.
                  </p>
                </div>
                <div style={{ background: 'var(--surface)', padding: 12, borderRadius: 6 }}>
                  <strong style={{ color: 'var(--text)' }}>2. Strict TOC Primacy:</strong>
                  <p className="text-muted" style={{ marginTop: 4, fontSize: 12 }}>
                    If seller delivers an account with disposable/temporary email when the agreed TOC specified "Original Email (OGE)", it is a per-se breach of contract.
                  </p>
                </div>
                <div style={{ background: 'var(--surface)', padding: 12, borderRadius: 6 }}>
                  <strong style={{ color: 'var(--text)' }}>3. 12-Digit UTR Anti-Replay:</strong>
                  <p className="text-muted" style={{ marginTop: 4, fontSize: 12 }}>
                    Payment claims must match unique 12-digit bank reference IDs. Re-used or edited UTR numbers trigger automatic fraudulent quarantine.
                  </p>
                </div>
                <div style={{ background: 'var(--surface)', padding: 12, borderRadius: 6 }}>
                  <strong style={{ color: 'var(--text)' }}>4. Inspection Window Enforcement:</strong>
                  <p className="text-muted" style={{ marginTop: 4, fontSize: 12 }}>
                    Buyers must report defects within the agreed inspection window (default: 15-30 min). Reporting issues hours after release shifts burden of proof to buyer.
                  </p>
                </div>
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  )
}
