import { useState } from 'react'
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import { clearToken } from '../lib/api'
import Overview from './sections/Overview'
import Deals from './sections/Deals'
import Disputes from './sections/Disputes'
import Users from './sections/Users'
import Guillotine from './sections/Guillotine'
import Groups from './sections/Groups'
import Settings from './sections/Settings'
import Audit from './sections/Audit'
import Templates from './sections/Templates'
import DataIO from './sections/DataIO'
import Middlemen from './sections/Middlemen'
import Rooms from './sections/Rooms'
import AIJudge from './sections/AIJudge'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

const NAV = [
  { section: 'CORE', items: [
    { path: '/', label: 'Overview', icon: '📊' },
    { path: '/deals', label: 'Deals', icon: '🤝', statKey: 'active_deals' },
    { path: '/disputes', label: 'Disputes', icon: '⚖️', statKey: 'open_disputes' },
    { path: '/ai-judge', label: 'Gemini AI Judge', icon: '🤖' },
  ]},
  { section: 'OPERATIONS', items: [
    { path: '/templates', label: 'Deal Templates', icon: '📋' },
  ]},
  { section: 'USERS', items: [
    { path: '/users', label: 'All Users', icon: '👥' },
    { path: '/guillotine', label: 'Guillotine ☠️', icon: '🔪' },
    { path: '/groups', label: 'Groups', icon: '💬' },
    { path: '/middlemen', label: 'Middlemen', icon: '🛡️' },
    { path: '/rooms', label: 'Room Pool', icon: '🚪' },
  ]},
  { section: 'SYSTEM', items: [
    { path: '/settings', label: 'Settings', icon: '⚙️' },
    { path: '/audit', label: 'Audit Log', icon: '📜' },
    { path: '/data', label: 'Data I/O', icon: '🗃️' },
  ]},
]

export default function Dashboard() {
  const nav = useNavigate()
  const loc = useLocation()

  const { data: stats } = useQuery({
    queryKey: ['stats'],
    queryFn: () => api.get('/stats').then(r => r.data),
    refetchInterval: 30000,
  })

  const getBadge = (item) => {
    if (!stats) return null
    if (item.statKey) {
      const v = stats[item.statKey]
      if (v > 0) return v
    }
    if (item.statKeys) {
      const total = item.statKeys.reduce((a, k) => a + (stats[k] || 0), 0)
      if (total > 0) return total
    }
    return null
  }

  const logout = () => {
    clearToken()
    nav('/login')
  }

  const currentSection = NAV.flatMap(s => s.items).find(item =>
    item.path === '/' ? loc.pathname === '/' : loc.pathname.startsWith(item.path)
  )

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <h1>🛡️ VAULT-PAY</h1>
          <p>God-Mode Admin Panel</p>
        </div>
        <nav className="sidebar-nav">
          {NAV.map(section => (
            <div className="nav-section" key={section.section}>
              <div className="nav-section-title">{section.section}</div>
              {section.items.map(item => {
                const badge = getBadge(item)
                const isActive = item.path === '/' ? loc.pathname === '/' : loc.pathname.startsWith(item.path)
                return (
                  <button
                    key={item.path}
                    className={`nav-item${isActive ? ' active' : ''}`}
                    onClick={() => nav(item.path)}
                  >
                    <span>{item.icon}</span>
                    <span>{item.label}</span>
                    {badge > 0 && (
                      <span className={`nav-badge${item.statKey?.includes('pending') || item.statKeys ? ' amber' : ''}`}>
                        {badge}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>
        <div style={{ padding: '12px 8px', borderTop: '1px solid var(--border)' }}>
          <button className="nav-item" onClick={logout} style={{ color: 'var(--danger)' }}>
            <span>🚪</span>
            <span>Logout</span>
          </button>
        </div>
      </aside>

      <div className="main-content">
        <div className="topbar">
          <div>
            <div className="topbar-title">{currentSection?.label || 'Dashboard'}</div>
          </div>
          <div className="topbar-right">
            {stats && (
              <>
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                  Active: <strong>{stats.active_deals}</strong>
                </span>
              </>
            )}
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--accent)', boxShadow: '0 0 6px var(--accent)' }} title="System Online" />
          </div>
        </div>

        <div className="page-body">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/deals" element={<Deals />} />
            <Route path="/disputes" element={<Disputes />} />
            <Route path="/ai-judge" element={<AIJudge />} />
            <Route path="/users" element={<Users />} />
            <Route path="/guillotine" element={<Guillotine />} />
            <Route path="/groups" element={<Groups />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/templates" element={<Templates />} />
            <Route path="/data" element={<DataIO />} />
            <Route path="/middlemen" element={<Middlemen />} />
            <Route path="/rooms" element={<Rooms />} />
          </Routes>
        </div>
      </div>
    </div>
  )
}
