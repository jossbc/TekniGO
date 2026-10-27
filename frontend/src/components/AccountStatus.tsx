import { Link } from 'react-router-dom'
import { signOut } from 'firebase/auth'
import { auth } from '../lib/firebase'
import type { AccountState } from '../lib/account'
import { Button } from './ui'
import { useState } from 'react'

export function AccountStatus({ state, retry }: { state: AccountState; retry: () => void }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function loginAgain() {
    setBusy(true)
    setError('')
    try {
      if (auth) await signOut(auth)
    } catch {
      setError('No pudimos cerrar la sesión. Intenta nuevamente.')
    } finally {
      setBusy(false)
    }
  }
  if (state.status === 'loading') return <p role="status">Cargando tu cuenta…</p>
  if (state.status === 'missing') return <>
    <p>Completa tu registro para acceder a tu perfil.</p>
    <Link className="button" to="/registro">Completar mi registro</Link>
  </>
  if (state.status === 'unauthorized') return <>
    <p role="alert">No pudimos validar tu sesión. Reintenta o vuelve a iniciar sesión.</p>
    {error && <p role="alert">{error}</p>}
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 16 }}>
      <Button onClick={retry} disabled={busy} style={{ width: 'auto' }}>Reintentar</Button>
      <Button className="button-outline" onClick={loginAgain} disabled={busy} style={{ width: 'auto' }}>
        {busy ? 'Cerrando sesión…' : 'Volver a iniciar sesión'}
      </Button>
    </div>
  </>
  if (state.status === 'error') return <>
    <p role="alert">{state.message}</p>
    <Button onClick={retry}>Reintentar</Button>
  </>
  return null
}
