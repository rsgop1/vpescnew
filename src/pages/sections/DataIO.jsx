import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../../lib/api'

const COLLECTIONS = [
  { id: 'users', label: '👥 Users' },
  { id: 'deals', label: '🤝 Deals' },
  { id: 'transactions', label: '💳 Transactions' },
  { id: 'disputes', label: '⚖️ Disputes' },
  { id: 'blacklist', label: '🛑 Blacklist' },
  { id: 'groups_tbl', label: '💬 Groups' },
  { id: 'audit_log', label: '📜 Audit Log' },
  { id: 'vouches', label: '🏅 Vouches' },
  { id: 'referrals', label: '🔗 Referrals' },
  { id: 'deal_templates', label: '📋 Templates' },
]

export default function DataIO() {
  const [exportCol, setExportCol] = useState('deals')
  const [exportData, setExportData] = useState(null)

  const exportMut = useMutation({
    mutationFn: (col) => api.get(`/export/${col}`).then(r => r.data),
    onSuccess: (data) => {
      setExportData(data)
      toast.success(`Exported ${data.count} records!`)
    },
    onError: (e) => toast.error(e.response?.data?.detail || 'Error'),
  })

  const downloadJson = () => {
    if (!exportData) return
    const blob = new Blob([JSON.stringify(exportData.data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${exportData.collection}_${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const downloadCsv = () => {
    if (!exportData || !exportData.data.length) return
    const keys = Object.keys(exportData.data[0])
    const rows = [keys.join(','), ...exportData.data.map(r =>
      keys.map(k => {
        const v = r[k]
        if (v === null || v === undefined) return ''
        const str = typeof v === 'object' ? JSON.stringify(v) : String(v)
        return str.includes(',') || str.includes('\n') ? `"${str.replace(/"/g, '""')}"` : str
      }).join(',')
    )]
    const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${exportData.collection}_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">🗃️ Data Export</div>
          <div className="section-sub">Export collections as JSON or CSV</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 20 }}>
        <div className="card">
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>📤 Export Collection</div>
          <div className="form-group">
            <label className="form-label">Collection</label>
            <select value={exportCol} onChange={e => { setExportCol(e.target.value); setExportData(null) }}>
              {COLLECTIONS.map(c => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </select>
          </div>
          <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', marginBottom: 10 }}
            onClick={() => exportMut.mutate(exportCol)} disabled={exportMut.isPending}>
            {exportMut.isPending ? '⏳ Loading...' : '📤 Export Data'}
          </button>

          {exportData && (
            <div style={{ marginTop: 12, padding: 12, background: 'var(--surface)', borderRadius: 8 }}>
              <div style={{ fontSize: 13, marginBottom: 10 }}>
                ✅ <strong>{exportData.count}</strong> records from <strong>{exportData.collection}</strong>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-secondary btn-sm" onClick={downloadJson}>📄 JSON</button>
                <button className="btn btn-secondary btn-sm" onClick={downloadCsv}>📊 CSV</button>
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>📊 Quick Preview</div>
          {exportData && exportData.data.length > 0 ? (
            <div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
                Showing first 5 of {exportData.count} records
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ fontSize: 11 }}>
                  <thead>
                    <tr>
                      {Object.keys(exportData.data[0]).slice(0, 7).map(k => (
                        <th key={k}>{k}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {exportData.data.slice(0, 5).map((row, i) => (
                      <tr key={i}>
                        {Object.keys(exportData.data[0]).slice(0, 7).map(k => (
                          <td key={k} style={{ maxWidth: 100 }}>
                            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 100 }}>
                              {row[k] === null ? '—' : typeof row[k] === 'object' ? '...' : String(row[k]).slice(0, 20)}
                            </div>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <div className="empty">
              <div className="empty-icon">📊</div>
              <div className="empty-text">Select a collection and click Export to preview data</div>
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>ℹ️ Data Information</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10 }}>
          {COLLECTIONS.map(c => (
            <div key={c.id} style={{ padding: '10px 14px', background: 'var(--surface)', borderRadius: 8, fontSize: 13 }}>
              <div style={{ fontWeight: 500 }}>{c.label}</div>
              <div className="text-muted" style={{ marginTop: 4 }}>{c.id}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
