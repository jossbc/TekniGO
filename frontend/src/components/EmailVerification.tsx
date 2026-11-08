import { useEffect, useRef, useState } from 'react'
import type { User } from 'firebase/auth'
import { errorMessage } from '../lib/api'
import { requestEmailVerification, refreshEmailVerification, resendWaitSeconds, watchEmailVerification, type EmailVerificationStatus } from '../lib/emailVerification'
import { Button } from './ui'

export default function EmailVerification({ user, initialNotice = '' }: { user: User; initialNotice?: string }) {
  const [status, setStatus] = useState<EmailVerificationStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(initialNotice)
  const [error, setError] = useState('')
  const [checkError, setCheckError] = useState('')
  const [wait, setWait] = useState(() => resendWaitSeconds(user))
  const generation = useRef(0)

  useEffect(() => {
    const current = ++generation.current
    setStatus(null)
    setError('')
    setCheckError('')
    setBusy(false)
    setWait(resendWaitSeconds(user))
    const timer = window.setInterval(() => setWait(resendWaitSeconds(user)), 1000)
    const stop = watchEmailVerification(user, value => {
      setStatus(value)
      setCheckError('')
      if (value.email_verified) {
        setNotice('')
        setError('')
        window.clearInterval(timer)
      }
    }, err => setCheckError(errorMessage(err)))
    return () => {
      if (generation.current === current) generation.current++
      stop()
      window.clearInterval(timer)
    }
  }, [user, user.email])

  async function resend() {
    if (busy) return
    const current = generation.current
    setBusy(true)
    setNotice('')
    setError('')
    try {
      const sent = await requestEmailVerification(user)
      if (generation.current !== current) return
      setWait(resendWaitSeconds(user))
      if (sent) setNotice('Enlace enviado. Revisa tu bandeja de entrada y spam. Al confirmar tu correo, esta pantalla se actualizará automáticamente.')
      else {
        const value = await refreshEmailVerification(user)
        if (generation.current === current) setStatus(value)
      }
    } catch (err) { if (generation.current === current) setError(errorMessage(err)) }
    finally { if (generation.current === current) setBusy(false) }
  }

  return <section className="account-card" style={{ marginBottom: 24 }} aria-label="Verificación de correo">
    <h2>Verifica tu correo</h2>
    <p className="email-value">{status?.email || user.email}</p>
    {status?.email_verified ? <p className="success-alert" role="status">Correo verificado.</p> : <>
      <p>{!status && !error && !checkError ? 'Comprobando tu correo.' : 'Abre el enlace que enviamos a tu correo. Esta pantalla se actualizará automáticamente cuando lo confirmes.'}</p>
      <Button type="button" onClick={resend} disabled={busy || wait > 0} style={{ width: 'auto' }}>
        {busy ? 'Enviando.' : wait > 0 ? `Reenviar en ${wait} s` : 'Reenviar enlace'}
      </Button>
    </>}
    {notice && !status?.email_verified && <p role="status">{notice}</p>}
    {checkError && !status?.email_verified && <p className="alert" role="alert">{checkError} Volveremos a comprobarlo automáticamente.</p>}
    {error && !status?.email_verified && <p className="alert" role="alert">{error}</p>}
  </section>
}
