import LoadingState from './components/LoadingState'
import { useState } from 'react'
import type { User } from 'firebase/auth'
import { Link, useNavigate } from 'react-router-dom'
import { useAccount } from './lib/useAccount'
import { errorMessage } from './lib/api'
import { registrationComplete, registrationRequirements } from './lib/registration'
import { AccountStatus } from './components/AccountStatus'
import ClientIdentityVerification from './components/ClientIdentityVerification'
import SpecialtySelector from './components/SpecialtySelector'
import CoverageZoneSelector from './CoverageZoneSelector'
import { Brand, Button } from './components/ui'

export default function CompleteRegistration({ user }: { user: User }) {
  const { state, retry } = useAccount(user)
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const profile = state.status === 'ready' ? state.profile : null

  async function finish() {
    if (!profile) return
    setBusy(true)
    setError('')
    try {
      const requirements = await registrationRequirements(user, profile)
      if (!registrationComplete(requirements)) {
        const missing = [
          !requirements.identityReady && 'enviar tu DNI por ambos lados y tu selfie',
          !requirements.specialtiesReady && 'guardar al menos una especialidad',
          !requirements.zonesReady && 'guardar al menos una zona de cobertura',
        ].filter(Boolean)
        setError(`Para finalizar debes ${missing.join(', ')}.`)
        return
      }
      navigate('/mi-cuenta', { replace: true, state: { registrationSuccess: true } })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (state.status === 'loading') return <LoadingState variant="screen" message="Cargando tu cuenta." />

  return <div className="site">
    <header className="header"><Brand /><Link to="/perfil" className="link">Mi perfil</Link></header>
    <main className="account" style={{ margin: '0 auto 60px' }}>
      {!profile ? <AccountStatus state={state} retry={retry} /> : <>
      <p className="mini-label">PASO 2 DE 2</p>
      <h1>Completa tu registro</h1>
      <p>Tu cuenta ya está creada. Envía tus documentos{profile?.roles.includes('TECNICO') ? ' y guarda los datos de tu perfil técnico' : ''} para finalizar. Tu identidad quedará pendiente de revisión.</p>
      {profile && <>
        <section className="account-card profile-card" style={{ marginBottom: 24 }}>
          <h2>Verificación de identidad</h2>
          <ClientIdentityVerification user={user} initialStatus={profile.estado_identidad} />
        </section>
        {profile.roles.includes('TECNICO') && <>
          <section className="account-card profile-card" style={{ marginBottom: 24 }}>
            <h2>Tus especialidades</h2>
            <p>Selecciona al menos una especialidad y presiona Guardar.</p>
            <SpecialtySelector user={user} />
          </section>
          <section className="account-card profile-card" style={{ marginBottom: 24 }}>
            <h2>Zonas de cobertura</h2>
            <p>Agrega al menos una zona a la lista y guarda tus zonas de cobertura.</p>
            <CoverageZoneSelector user={user} />
          </section>
        </>}
        {busy && <LoadingState variant="inline" message="Comprobando tus datos guardados." />}
        {error && <div className="alert" role="alert">{error}</div>}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', marginTop: 24 }}>
          <Button type="button" onClick={finish} disabled={busy} style={{ width: 'auto' }}>{busy ? 'Comprobando datos…' : 'Finalizar registro'}</Button>
          <Link className="link" to="/mi-cuenta">Continuar más tarde</Link>
        </div>
        <p>Los datos guardados se conservan para que puedas retomar este paso. Subir documentos no aprueba automáticamente tu identidad.</p>
      </>}
      </>}
    </main>
  </div>
}
