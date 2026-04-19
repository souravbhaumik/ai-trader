import { useEffect, useState } from 'react'

interface Props {
  symbol: string  // e.g. "RELIANCE.NS" or "RELIANCE"
  size?: number   // px, default 32
}

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export default function TickerLogo({ symbol, size = 32 }: Props) {
  const ticker = symbol.replace(/\.(NS|BO|BSE)$/i, '').toUpperCase()
  const logoUrl = `${API_BASE}/api/v1/logos/${encodeURIComponent(ticker)}`
  const [src, setSrc] = useState(logoUrl)

  // Reset src whenever the symbol changes (handles React component reuse)
  useEffect(() => { setSrc(logoUrl) }, [logoUrl])

  return (
    <img
      src={src}
      alt={ticker}
      width={size}
      height={size}
      onError={() => setSrc(`${API_BASE}/api/v1/logos/__fallback__`)}
      style={{
        width: size,
        height: size,
        borderRadius: '6px',
        flexShrink: 0,
        objectFit: 'contain',
        display: 'block',
      }}
    />
  )
}
