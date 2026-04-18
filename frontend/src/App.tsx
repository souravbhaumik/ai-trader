import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import { Cpu, LayoutDashboard, Activity, Settings, Terminal, BarChart2, Zap, LogOut, Briefcase, TrendingUp } from 'lucide-react'
import { useAuthStore } from './store/authStore'
import { apiClient } from './api/client'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import DashboardPage from './pages/DashboardPage'
import OpportunitiesPage from './pages/OpportunitiesPage'
import SignalLogPage from './pages/SignalLogPage'
import SettingsPage from './pages/SettingsPage'
import AdminPage from './pages/AdminPage'
import ScreenerPage from './pages/ScreenerPage'
import LivePortfolioPage from './pages/LivePortfolioPage'
import ForecastPage from './pages/ForecastPage'

// ── Auth guard ────────────────────────────────────────────────────────────────
function RequireAuth({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

// ── Admin guard ───────────────────────────────────────────────────────────────
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const isAdmin = useAuthStore((s) => s.isAdmin)
  if (!isAdmin()) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}

// ── Protected app shell ───────────────────────────────────────────────────────
function AppLayout() {
  const { user, clearAuth, tradingMode, setTradingMode, isAdmin } = useAuthStore()

  // Hydrate tradingMode from API on every page load so topbar stays in sync.
  useEffect(() => {
    apiClient.get<{ trading_mode: string }>('/settings')
      .then(r => setTradingMode(r.data.trading_mode as 'paper' | 'live'))
      .catch(() => { /* leave cached value */ })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleLogout() {
    try {
      await apiClient.post('/auth/logout')
    } catch {
      // ignore — clear local state regardless
    }
    clearAuth()
  }

  return (
    <div className="app-layout">
      {/* Topbar */}
      <header className="topbar">
        <div className="topbar-logo">
          <Cpu size={22} />
          AI Trader
        </div>
        <div className="topbar-right">
          <span className={`mode-badge ${tradingMode}`}>
            {tradingMode === 'live' ? '⚡ Live Trading' : '📄 Paper Trading'}
          </span>
          <div className="flex-center gap-2 text-sm text-muted">
            <span className="status-dot green" />
            Live
          </div>
          {user && (
            <button
              onClick={handleLogout}
              className="btn-outline btn flex-center gap-2"
              title="Sign out"
            >
              <LogOut size={14} />
              {user.full_name ?? user.email}
            </button>
          )}
        </div>
      </header>

      {/* Sidebar */}
      <nav className="sidebar">
        <NavLink to="/" end className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}>
          <LayoutDashboard size={16} /> Dashboard
        </NavLink>
        <NavLink to="/opportunities" className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}>
          <Zap size={16} /> Opportunities
        </NavLink>
        <NavLink to="/signals" className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}>
          <Activity size={16} /> Signal Log
        </NavLink>
        <NavLink to="/screener" className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}>
          <BarChart2 size={16} /> Market Screener
        </NavLink>
        <NavLink to="/live" className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}>
          <Briefcase size={16} /> Live Portfolio
          {tradingMode === 'live' && <span style={{ marginLeft: 'auto', width: 8, height: 8, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />}
        </NavLink>
        <NavLink to="/forecast" className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}>
          <TrendingUp size={16} /> AI Forecast
        </NavLink>
        {isAdmin() && (
          <NavLink to="/admin" className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}>
            <Terminal size={16} /> Pipeline
          </NavLink>
        )}
        <NavLink to="/settings" className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}>
          <Settings size={16} /> Settings
        </NavLink>
      </nav>

      {/* Main content */}
      <main className="main-content">
        <Routes>
          <Route path="/"              element={<DashboardPage />} />
          <Route path="/opportunities" element={<OpportunitiesPage />} />
          <Route path="/screener"      element={<ScreenerPage />} />
          <Route path="/signals"       element={<SignalLogPage />} />
          <Route path="/live"          element={<LivePortfolioPage />} />
          <Route path="/forecast"      element={<ForecastPage />} />
          <Route path="/admin"         element={<RequireAdmin><AdminPage /></RequireAdmin>} />
          <Route path="/settings"      element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}

// ── Root — silent session restore on every page load ─────────────────────────
export default function App() {
  // Three-state: 'pending' while the refresh call is in-flight,
  // 'done' once resolved (success or failure). Prevents flash-to-login.
  const [authReady, setAuthReady] = useState(false)
  const { user, setAuth, clearAuth } = useAuthStore()

  useEffect(() => {
    // If there's a persisted user profile, try to silently exchange the
    // httpOnly refresh-token cookie for a fresh JWT access token.
    // If the cookie is expired/absent the call will 401, we clear state and
    // send them to /login.
    if (user) {
      apiClient
        .post<{ access_token: string; user: typeof user }>('/auth/refresh')
        .then(({ data }) => setAuth(data.user, data.access_token))
        .catch(() => clearAuth())
        .finally(() => setAuthReady(true))
    } else {
      setAuthReady(true)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Hold rendering until we know whether the session is valid.
  // This prevents the router briefly flashing /login on every hard refresh.
  if (!authReady) {
    return (
      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center',
        justifyContent: 'center', background: 'var(--bg-base)',
      }}>
        <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:16 }}>
          <svg width="40" height="40" viewBox="0 0 40 40">
            <circle cx="20" cy="20" r="16" fill="none" stroke="var(--border-light)" strokeWidth="3"/>
            <circle cx="20" cy="20" r="16" fill="none" stroke="var(--green)" strokeWidth="3"
              strokeDasharray="60 40" strokeLinecap="round">
              <animateTransform attributeName="transform" type="rotate"
                from="0 20 20" to="360 20 20" dur="0.9s" repeatCount="indefinite"/>
            </circle>
          </svg>
          <span style={{ color:'var(--text-muted)', fontSize:13 }}>Restoring session…</span>
        </div>
      </div>
    )
  }

  return (
    <BrowserRouter>
      <Toaster
        position="top-right"
        toastOptions={{
          style: {
            background: '#1a2235',
            color: '#f1f5f9',
            border: '1px solid #1e2d45',
          },
        }}
      />
      <Routes>
        {/* Public routes */}
        <Route path="/login"    element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* Protected routes */}
        <Route
          path="/*"
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
