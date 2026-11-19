import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

const StatCard = ({ label, value, sub, color = '' }) => (
  <div className={`stat-card ${color}`}>
    <div className="stat-label">{label}</div>
    <div className="stat-value">{value}</div>
    {sub && <div className="stat-sub">{sub}</div>}
  </div>
)

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    return (
      <div className="card" style={{ padding: '8px 12px', fontSize: 12 }}>
        <p style={{ color: 'var(--muted)' }}>{label}</p>
        <p style={{ color: 'var(--accent)' }}>₹{payload[0]?.value?.toLocaleString()}</p>
      </div>
    )
  }
  return null
}

export default function Overview() {
  const { data: stats, isLoading } = useQuery({
    queryKey: ['stats'],
    queryFn: () => api.get('/stats').then(r => r.data),
    refetchInterval: 15000,
  })

  if (isLoading) return <div className="loading"><div className="spinner" /><span>Loading...</span></div>

  const chartData = stats?.revenue_chart?.map(r => ({
    day: r.day?.slice(5),
    revenue: r.total || 0,
  })).reverse() || []

  const recentDeals = stats?.recent_deals || []

  const statusColor = {
    completed: 'badge-green', awaiting_payment: 'badge-yellow',
    paid: 'badge-blue', disputed: 'badge-red', cancelled: 'badge-gray',
    awaiting_acceptance: 'badge-gray',
  }

  return (
    <div>
      <div className="section-header">
        <div>
          <div className="section-title">📊 Operations Overview</div>
          <div className="section-sub">Real-time manual-middleman escrow activity</div>
        </div>
      </div>

      <div className="stat-grid">
        <StatCard label="🤝 Active Deals" value={stats?.active_deals || 0} sub="Ongoing escrows" color="purple" />
        <StatCard label="👥 Total Users" value={(stats?.total_users || 0).toLocaleString()} sub="Registered users" />
        <StatCard label="🚨 Open Disputes" value={stats?.open_disputes || 0} sub="Awaiting resolution" color="red" />
        <StatCard label="📈 Total Deals" value={(stats?.total_deals || 0).toLocaleString()} sub="All time" />
      </div>

      <div style={{ marginBottom: 24 }}>
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>📈 Revenue (Last 7 Days)</div>
          </div>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#00d084" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#00d084" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2738" />
                <XAxis dataKey="day" tick={{ fill: '#64748b', fontSize: 11 }} />
                <YAxis tick={{ fill: '#64748b', fontSize: 11 }} />
                <Tooltip content={<CustomTooltip />} />
                <Area type="monotone" dataKey="revenue" stroke="#00d084" fill="url(#rev)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty"><div className="empty-icon">📊</div><div className="empty-text">No revenue data yet</div></div>
          )}
        </div>
      </div>

      <div className="card">
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>🕐 Recent Deals</div>
        {recentDeals.length === 0 ? (
          <div className="empty"><div className="empty-icon">🤝</div><div className="empty-text">No deals yet</div></div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Deal ID</th><th>Buyer</th><th>Seller</th><th>Amount</th><th>Status</th><th>Date</th>
                </tr>
              </thead>
              <tbody>
                {recentDeals.map(d => (
                  <tr key={d.deal_id}>
                    <td className="font-mono">#{d.deal_id}</td>
                    <td>@{d.buyer_un || '—'}</td>
                    <td>@{d.seller_un || '—'}</td>
                    <td style={{ color: 'var(--accent)' }}>₹{(d.amount || 0).toLocaleString()}</td>
                    <td><span className={`badge ${statusColor[d.status] || 'badge-gray'}`}>{d.status}</span></td>
                    <td className="text-muted">{d.created_at?.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
