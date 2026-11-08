import { useEffect, useRef, useState, type FormEvent } from 'react'
import { RecaptchaVerifier, type User } from 'firebase/auth'
import { Link } from 'react-router-dom'
import { auth, phoneTestMode } from '../lib/firebase'
import { api } from '../lib/api'
import { sendPhoneCode, confirmPhoneCode, phoneVerificationErrorMessage, type PhoneStatus } from '../lib/phoneVerification'
import { Button, Field } from './ui'

export default function PhoneVerification({ user }: { user: User }) {
  const [status, setStatus] = useState<PhoneStatus | null>(null)
  const [verificationId, setVerificationId] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [waitUntil, setWaitUntil] = useState(0)
  const [now, setNow] = useState(Date.now())
  const container = useRef<HTMLDivElement>(null)
  const verifier = useRef<RecaptchaVerifier | null>(null)
  const active = useRef(true)
  const wait = Math.max(0, Math.ceil((waitUntil - now) / 1000))

  useEffect(() => {
    active.current = true
    let current = true
    api<PhoneStatus>('/users/me/phone-verification', user, {})
      .then(value => { if (current) setStatus(value) })
      .catch(err => { if (current) setError(phoneVerificationErrorMessage(err)) })
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => {
      current = false
      active.current = false
      window.clearInterval(timer)
      verifier.current?.clear()
      verifier.current = null
    }
  }, [user])

  async function send() {
    if (busy || wait || !auth || !container.current) return
    setBusy(true); setError(''); setNotice('')
    try {
      verifier.current?.clear()
      verifier.current = new RecaptchaVerifier(auth, container.current, { size: 'normal' })
      const id = await sendPhoneCode(user, verifier.current)
      if (!active.current) return
      setVerificationId(id)
      setCode('')
      setWaitUntil(Date.now() + 60_000)
      setNow(Date.now())
      setNotice(phoneTestMode ? 'Solicitud de prueba preparada. Ingresa el código que configuraste para este número en Firebase; no se envía un SMS.' : 'Código enviado por SMS. Ingresa el código más reciente.')
    } catch (err) {
      if (active.current) {
        setError(phoneVerificationErrorMessage(err))
      }
    } finally {
      if (active.current) { setBusy(false); verifier.current?.clear(); verifier.current = null }
    }
  }

  async function confirm(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const value = await confirmPhoneCode(user, verificationId, code)
      if (!active.current) return
      setStatus(value)
      if (!value.telefono_verificado) setNotice('El número registrado cambió. Solicita un código para tu número actual.')
      setVerificationId('')
    } catch (err) { if (active.current) setError(phoneVerificationErrorMessage(err)) }
    finally { if (active.current) setBusy(false) }
  }

  async function check() {
    setBusy(true); setError('')
    try { setStatus(await api<PhoneStatus>('/users/me/phone-verification', user, {})) }
    catch (err) { setError(phoneVerificationErrorMessage(err)) }
    finally { setBusy(false) }
  }

  return <section className="account-card" style={{ marginBottom: 24 }} aria-label="Verificación de teléfono">
    <h2>Verifica tu teléfono</h2>
    <p>{status?.telefono || 'Consultando tu número registrado.'}</p>
    {status?.telefono_verificado ? <p className="success-alert" role="status">Teléfono verificado.</p> : <>
      <p>{phoneTestMode ? 'Ingresa el codigo correcto' : 'Te enviaremos un código por SMS al número de tu perfil. Al continuar, aceptas que Google procese tu número para prevenir abuso. Pueden aplicar cargos de tu operador.'}</p>
      <p><Link to="/perfil">Cambiar mi número</Link></p>
      <div ref={container} />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBlock: 16 }}>
        <Button type="button" disabled={busy || !status || !auth || wait > 0} onClick={send} style={{ width: 'auto' }}>
          {wait ? `Reenviar en ${wait} s` : verificationId ? 'Reenviar código' : 'Enviar código'}
        </Button>
        <Button type="button" className="button-outline" disabled={busy} onClick={check} style={{ width: 'auto' }}>Comprobar estado</Button>
      </div>
      {verificationId && <form onSubmit={confirm} style={{ maxWidth: 320 }}>
        <Field label={phoneTestMode ? 'Código de prueba' : 'Código SMS'} autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={e => setCode(e.target.value)} />
        <Button type="submit" disabled={busy} style={{ width: 'auto' }}>Confirmar código</Button>
      </form>}
    </>}
    {notice && !status?.telefono_verificado && <p role="status">{notice}</p>}
    {error && !status?.telefono_verificado && <p className="alert" role="alert">{error}</p>}
  </section>
}
