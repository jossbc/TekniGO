import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, LayoutDashboard, UserRound } from 'lucide-react'
import { auth } from '../lib/firebase'
import LogoutButton from './LogoutButton'

export default function AccountMenu({ name, email, accountHref = '/perfil', accountLabel = 'Mi perfil' }: {
  name?: string; email?: string | null; accountHref?: string; accountLabel?: string
}) {
  const [open, setOpen] = useState(false)
  const container = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const id = useId()
  const address = email || auth?.currentUser?.email
  const parts = (name || '').trim().split(/\s+/).filter(Boolean)
  const initials = (parts.length > 1 ? parts[0][0] + parts.at(-1)![0] : parts[0]?.[0] || address?.[0] || '?').toUpperCase()

  useEffect(() => {
    if (!open) return
    panel.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
    function outside(event: PointerEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault(); setOpen(false); trigger.current?.focus()
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return <div className="account-menu" ref={container} onBlur={event => {
    if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false)
  }}>
    <button type="button" className="account-menu-trigger" ref={trigger} aria-label="Abrir menú de cuenta"
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(value => !value)}>
      <span className="account-menu-avatar" aria-hidden="true">{initials}</span><ChevronDown size={14} aria-hidden="true" />
    </button>
    {open && <div className="account-menu-panel" id={id} role="menu" aria-label="Opciones de cuenta" ref={panel} onKeyDown={event => {
      if (!['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return
      const items = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)')]
      if (!items.length) return
      event.preventDefault()
      const index = items.indexOf(document.activeElement as HTMLElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
        (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
      items[next].focus()
    }}>
      <div className="account-menu-identity"><strong>{name || 'Tu cuenta'}</strong>{address && <span>{address}</span>}</div>
      {accountHref === '/perfil' && <Link className="account-menu-item" role="menuitem" to="/mi-cuenta" onClick={() => setOpen(false)}><LayoutDashboard size={16} />Mi cuenta</Link>}
      <Link className="account-menu-item" role="menuitem" to={accountHref} onClick={() => setOpen(false)}><UserRound size={16} />{accountLabel}</Link>
      <div className="account-menu-divider" />
      <LogoutButton variant="menu" />
    </div>}
  </div>
}
