import { useState, FormEvent } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { apiClient } from '../api/client'
import { useAuthStore } from '../store/authStore'

interface LoginResponseData {
  access_token: string
  user: {
    id: string
    email: string
    full_name: string | null
    role: string
    is_active: boolean
    is_totp_configured: boolean
    is_live_trading_enabled: boolean
  }
}

export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  // Redirect already-authenticated users to home
  if (isAuthenticated()) return <Navigate to="/" replace />

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [showTotp, setShowTotp] = useState(false)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setLoading(true)

    try {
      const { data } = await apiClient.post<LoginResponseData>('/auth/login', {
        email,
        password,
        totp_code: showTotp ? totpCode : undefined,
      })
      setAuth(data.user, data.access_token)
      navigate('/', { replace: true })
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number }})?.response?.status
      const detail: string =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? ''

      if (status === 401 && detail.toLowerCase().includes('totp')) {
        setShowTotp(true)
        toast.error('Enter your 6-digit authenticator code.')
      } else {
        toast.error(detail || 'Login failed. Check your credentials.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">AI Trader</h1>
        <p className="login-subtitle">Sign in to your account</p>

        <form onSubmit={handleSubmit} className="login-form" noValidate>
          <div className="form-group">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
              disabled={loading}
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              disabled={loading}
            />
          </div>

          {showTotp && (
            <div className="form-group">
              <label htmlFor="totp">Authenticator Code</label>
              <input
                id="totp"
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                autoComplete="one-time-code"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ''))}
                placeholder="000000"
                required
                autoFocus
                disabled={loading}
              />
            </div>
          )}

          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
