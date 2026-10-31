import { useEffect, useState } from 'react'
import { Wrench } from 'lucide-react'

type Props = {
  message?: string
  detail?: string
  variant?: 'screen' | 'section' | 'inline'
}

export default function LoadingState({ message = 'Preparando tu espacio.', detail, variant = 'section' }: Props) {
  const [takingLonger, setTakingLonger] = useState(false)
  useEffect(() => {
    setTakingLonger(false)
    const timer = window.setTimeout(() => setTakingLonger(true), 12000)
    return () => window.clearTimeout(timer)
  }, [message])

  const content = <div className={`wait-state wait-state--${variant}`} role="status" aria-live="polite" aria-atomic="true">
    <span className="wait-state__symbol" aria-hidden="true"><Wrench size={24} /><span className="wait-state__ring" /></span>
    <div className="wait-state__copy">
      <strong>{message}</strong>
      {detail && <p>{detail}</p>}
      {takingLonger && <p>Está tardando un poco más de lo habitual. Seguimos esperando la respuesta.</p>}
    </div>
  </div>
  return variant === 'screen' ? <main className="wait-screen">{content}</main> : content
}
