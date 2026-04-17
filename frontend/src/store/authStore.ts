import { create } from 'zustand'

interface AuthUser {
  id: string
  email: string
  full_name: string | null
  role: string
  is_active: boolean
  is_totp_configured: boolean
  is_live_trading_enabled: boolean
}

interface AuthState {
  user: AuthUser | null
  accessToken: string | null

  setAuth: (user: AuthUser, accessToken: string) => void
  setAccessToken: (token: string) => void
  clearAuth: () => void
  isAuthenticated: () => boolean
  isAdmin: () => boolean
}

const USER_KEY = 'ai_trader_user'

function loadUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: loadUser(),
  // Access token (JWT) lives in memory only — never persisted to disk.
  // On page reload, App.tsx calls /auth/refresh using the httpOnly cookie
  // to silently restore a fresh JWT without requiring re-login.
  accessToken: null,

  setAuth: (user, accessToken) => {
    localStorage.setItem(USER_KEY, JSON.stringify(user))
    set({ user, accessToken })
  },

  setAccessToken: (accessToken) => set({ accessToken }),

  clearAuth: () => {
    localStorage.removeItem(USER_KEY)
    set({ user: null, accessToken: null })
  },

  isAuthenticated: () => get().accessToken !== null,

  isAdmin: () => get().user?.role === 'admin',
}))

