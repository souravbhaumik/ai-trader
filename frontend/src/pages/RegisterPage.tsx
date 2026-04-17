import { useState, FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { apiClient } from '../api/client'
import { useAuthStore } from '../store/authStore'

interface RegisterResponseData {
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

export default function RegisterPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const setAuth = useAuthStore((s) => s.setAuth)

  const inviteToken = searchParams.get('token') ?? ''
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [loading, setLoading] = useState(false)

  if (!inviteToken) {
    return (
      <div className="login-page">
        <div className="login-card">
          <h1 className="login-title">Invalid Link</h1>
          <p className="login-subtitle">This registration link is missing or malformed.</p>
        </div>
      </div>
    )
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (password !== confirm) {
      toast.error('Passwords do not match.')
      return
    }
    setLoading(true)
    try {
      const { data } = await apiClient.post<RegisterResponseData>('/auth/register', {
        invite_token: inviteToken,
        full_name: fullName,
        password,
      })
      setAuth(data.user, data.access_token)
      toast.success('Account created! Welcome.')
      navigate('/', { replace: true })
    } catch (err: unknown) {
      const detail: string =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Registration failed.'
      toast.error(detail)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">Create Account</h1>
        <p className="login-subtitle">Complete your registration</p>

        <form onSubmit={handleSubmit} className="login-form" noValidate>
          <div className="form-group">
            <label htmlFor="name">Full Name</label>
            <input
              id="name"
              type="text"
              autoComplete="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Your Name"
              required
              disabled={loading}
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Min 8 chars, 1 uppercase, 1 digit"
              required
              disabled={loading}
            />
          </div>

          <div className="form-group">
            <label htmlFor="confirm">Confirm Password</label>
            <input
              id="confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="Re-enter password"
              required
              disabled={loading}
            />
          </div>

          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? 'Creating account…' : 'Create Account'}
          </button>
        </form>
      </div>
    </div>
  )
}
