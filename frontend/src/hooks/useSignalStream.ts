/**
 * useSignalStream — subscribe to /ws/signals and receive new AI signals in real-time.
 *
 * Returns an array of signals, newest first.  Any signal received over the
 * socket is prepended to the list (capped at `maxSignals` to avoid memory growth).
 * The caller can merge this with the initial REST-fetched list.
 */
import { useEffect, useRef, useState } from 'react'
import { useAuthStore } from '../store/authStore'

const BASE_WS = import.meta.env.VITE_WS_URL
  ?? (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/^http/, 'ws')

const RECONNECT_DELAY_MS = 3_000
const MAX_SIGNALS        = 200

export interface LiveSignal {
  id: string
  symbol: string
  ts: string
  signal_type: 'BUY' | 'SELL' | 'HOLD'
  confidence: number
  entry_price: number | null
  target_price: number | null
  stop_loss: number | null
  model_version: string
  is_active: boolean
}

export function useSignalStream(enabled = true) {
  const [signals, setSignals]   = useState<LiveSignal[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef  = useRef<WebSocket | null>(null)
  const token  = useAuthStore(s => s.accessToken)

  useEffect(() => {
    if (!enabled || !token) return

    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    function connect() {
      if (cancelled) return

      const url = `${BASE_WS}/api/v1/ws/signals?token=${encodeURIComponent(token!)}`
      const ws  = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        if (!cancelled) setConnected(true)
      }

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as { type: string; data?: LiveSignal }
          if (msg.type === 'signal' && msg.data) {
            setSignals(prev => [msg.data!, ...prev].slice(0, MAX_SIGNALS))
          }
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = () => {
        setConnected(false)
        if (!cancelled) {
          retryTimer = setTimeout(connect, RECONNECT_DELAY_MS)
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    }

    connect()

    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
      wsRef.current?.close()
      setConnected(false)
    }
  }, [enabled, token])

  return { signals, connected }
}
