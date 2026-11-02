import { useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { api, errorMessage } from '../lib/api'
import { requestEmailVerification, refreshEmailVerification, resendWaitSeconds, type EmailVerificationStatus } from '../lib/emailVerification'
import { Button } from './ui'

export default function EmailVerification({ user, initialNotice = '' }: { user: User; initialNotice?: string }) {
  const [status, setStatus] = useState<EmailVerificationStatus | null>(null)
  const [busy, setBusy] = useState<'send' | 'check' | null>(null)
  const [notice, setNotice] = useState(initialNotice)
  const [error, setError] = useState('')
  const [wait, setWait] = useState(() => resendWaitSeconds(user))

  useEffect(() => {
    let active = true
    setStatus(null)
    setError('')
    api<EmailVerificationStatus>('/users/me/email-verification', user)
      .then((value) => { if (active) setStatus(value) })
      .catch((err) => { if (active) setError(errorMessage(err)) })
    const timer = window.setInterval(() => setWait(resendWaitSeconds(user)), 1000)
    return () => { active = false; window.clearInterval(timer) }
  }, [user])

  async function act(action: 'send' | 'check') {
    if (busy) return
    setBusy(action)
    setNotice('')
    setError('')
    try {
      if (action === 'send') {
        const sent = await requestEmailVerification(user)
        setWait(resendWaitSeconds(user))
        if (sent) setNotice('Enlace enviado. Revisa tu bandeja de entrada y spam. Después de abrirlo, vuelve y pulsa «Ya verifiqué mi correo».')
        else setStatus(await refreshEmailVerification(user))
      } else {
        const value = await refreshEmailVerification(user)
        setStatus(value)
        if (!value.email_verified) setNotice('El correo sigue pendiente. Abre el enlace más reciente y vuelve a comprobarlo. Si venció, solicita uno nuevo.')
      }
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(null) }
  }

  return <section className="account-card" style={{ marginBottom: 24 }} aria-label="Verificación de correo">
    <h2>Verifica tu correo</h2>
    <p className="email-value">{status?.email || user.email}</p>
    {status?.email_verified ? <p className="success-alert" role="status">Correo verificado.</p> : <>
      <p>{!status && !error ? 'Comprobando tu correo…' : 'Confirma tu correo mediante el enlace. Esta confirmación es independiente de la revisión de tu identidad.'}</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        <Button type="button" onClick={() => act('send')} disabled={!!busy || wait > 0} style={{ width: 'auto' }}>
          {busy === 'send' ? 'Enviando…' : wait > 0 ? `Reenviar en ${wait} s` : 'Enviar o reenviar enlace'}
        </Button>
        <Button type="button" className="button-outline" onClick={() => act('check')} disabled={!!busy} style={{ width: 'auto' }}>
          {busy === 'check' ? 'Comprobando…' : 'Ya verifiqué mi correo'}
        </Button>
      </div>
    </>}
    {notice && <p role="status">{notice}</p>}
    {error && <p className="alert" role="alert">{error}</p>}
  </section>
}
