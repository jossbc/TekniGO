import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { errorMessage } from '../lib/api'
import { logoutCurrentSession } from '../lib/logout'
import { Button } from './ui'

export default function LogoutButton({ variant = 'button' }: { variant?: 'button' | 'menu' }) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  async function logout() {
    if (pending.current) return
    pending.current = true
    setBusy(true); setError('')
    try {
      await logoutCurrentSession()
      navigate('/ingresar', { replace: true })
    } catch (err) { setError(errorMessage(err)) }
    finally { pending.current = false; setBusy(false) }
  }
  return <span className="session-control">
    <Button type="button" className={variant === 'menu' ? 'account-menu-item account-menu-logout' : 'button-outline session-logout'} role={variant === 'menu' ? 'menuitem' : undefined} onClick={logout} disabled={busy} aria-label="Cerrar sesión">
      <LogOut size={16} aria-hidden="true" /><span>{busy ? 'Cerrando…' : 'Cerrar sesión'}</span>
    </Button>
    {error && <span className="alert session-error" role="alert">{error}</span>}
  </span>
}
