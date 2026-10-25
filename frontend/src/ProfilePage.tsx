import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { signOut, type User } from 'firebase/auth'
import { ArrowLeft, CheckCircle2, CircleAlert, Image as ImageIcon, Lock } from 'lucide-react'
import { auth } from './lib/firebase'
import { api, errorMessage, type Profile } from './lib/api'
import { Button, Field } from './components/ui'
import SpecialtySelector from './components/SpecialtySelector'
import { normalizeEmail, normalizeHonduranPhone, PHONE_HINT } from './lib/validation'

type ProfileChanges = { telefono?: string; correo?: string }
type Errors = { telefono?: string; correo?: string }

const ROLE_LABELS: Record<string, string> = { CLIENTE: 'Cliente', TECNICO: 'Técnico' }

const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))', gap: '2.25rem 3rem' }
// Los Field ya traen su propio margen inferior, por eso el formulario no lleva espacio vertical extra.
const formGrid = { ...grid, gap: '0 3rem' }
const card = { padding: '2.5rem' }
const cardTitle = { margin: '0 0 1.25rem', fontSize: '1.6rem' }
const inputStyle = { fontSize: '1rem', minHeight: 52 }
const lockedStyle = { ...inputStyle, background: '#f3f4f6', color: '#6b7280', cursor: 'not-allowed' }
const redBorder = { borderColor: '#b91c1c' }
const actionButton = { width: 'auto', padding: '0.8rem 1.6rem', fontSize: '1rem' }

function formatPhone(value: string) {
  const match = /^\+504(\d{4})(\d{4})$/.exec(value)
  return match ? `+504 ${match[1]}-${match[2]}` : value
}

function formatDni(value: string) {
  const match = /^(\d{4})(\d{4})(\d{5})$/.exec(value)
  return match ? `${match[1]}-${match[2]}-${match[3]}` : value
}

function check(fn: (value: string) => string, value: string) {
  try { fn(value); return '' }
  catch (err) { return (err as Error).message }
}

// Si el mensaje del backend habla solo del teléfono o solo del correo, va debajo de ese campo.
function fieldFromServer(message: string): Errors | null {
  const text = message.toLowerCase()
  if (text.startsWith('no pudimos')) return null
  const phone = text.includes('teléfono')
  const mail = text.includes('correo')
  if (phone && !mail) return { telefono: message }
  if (mail && !phone) return { correo: message }
  return null
}

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div style={{ fontWeight: 600, fontSize: '1rem' }}>{label}</div>
      <div style={{ fontSize: '1.3rem', marginTop: 6, overflowWrap: 'anywhere' }}>{children}</div>
    </div>
  )
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null
  return (
    <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 6, margin: '-10px 0 16px' }}>
      <CircleAlert size={15} /> {message}
    </p>
  )
}

export default function ProfilePage({ user, onName }: { user: User; onName?: (name: string) => void }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [errors, setErrors] = useState<Errors>({})

  useEffect(() => {
    let active = true
 api<Profile>('/users/me/personal', user)
      .then((value) => {
        if (!active) return
        setProfile(value)
        onName?.(value.nombre_completo)
      })
      .catch((err) => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [user, onName])

  function validateField(name: 'telefono' | 'correo', value: string) {
    const message = check(name === 'telefono' ? normalizeHonduranPhone : normalizeEmail, value)
    setErrors((prev) => ({ ...prev, [name]: message || undefined }))
  }

  function startEditing() {
    setNotice('')
    setError('')
    setErrors({})
    setEditing(true)
  }

  function cancelEditing() {
    setError('')
    setErrors({})
    setEditing(false)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!profile) return
    const form = new FormData(event.currentTarget)
    const telefonoRaw = String(form.get('telefono') || '')
    const correoRaw = String(form.get('correo') || '')

    setError('')
    setNotice('')
    const next: Errors = {
      telefono: check(normalizeHonduranPhone, telefonoRaw) || undefined,
      correo: check(normalizeEmail, correoRaw) || undefined,
    }
    setErrors(next)
    if (next.telefono || next.correo) return

    const telefono = normalizeHonduranPhone(telefonoRaw)
    const correo = normalizeEmail(correoRaw)
    const changes: ProfileChanges = {}
    if (telefono !== profile.telefono) changes.telefono = telefono
    if (correo !== profile.email.toLowerCase()) changes.correo = correo
    if (!changes.telefono && !changes.correo) {
      setNotice('No hiciste ningún cambio')
      setEditing(false)
      return
    }

    setBusy(true)
    try {
      const updated = await api<Profile>('/users/me', user, changes, 'PATCH')
      setProfile(updated)
      setEditing(false)
      setNotice('Cambios guardados correctamente')
      window.scrollTo({ top: 0, behavior: 'smooth' })
      if (changes.correo) {
        // Firebase puede invalidar la sesión al cambiar el correo.
        try { await user.reload(); await user.getIdToken(true) }
        catch { if (auth) await signOut(auth) }
      }
    } catch (err) {
      const message = errorMessage(err)
      const routed = fieldFromServer(message)
      if (routed) setErrors(routed)
      else setError(message)
    } finally {
      setBusy(false)
    }
  }

  const isTechnician = !!profile?.roles.includes('TECNICO')

  return (
    <main className="account" style={{ margin: '0 auto 90px' }}>
      {notice && (
        <div className="success-alert" role="status">
          <CheckCircle2 size={20} />
          <span>{notice}</span>
        </div>
      )}
      {error && <div className="alert" role="alert">{error}</div>}

      <h1 style={{ marginTop: 0 }}>{editing ? 'Editar perfil' : 'Mi perfil'}</h1>
      {!notice && <p>{editing ? 'Puedes cambiar tu correo y tu teléfono.' : 'Revisa los datos que tienes registrados en TekniGO.'}</p>}

      {!profile && !error && <p role="status">Cargando tu perfil…</p>}

      {profile && !editing && (
        <div style={{ display: 'grid', gap: '1.5rem' }}>
          <section className="account-card" style={card}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, marginBottom: '1.75rem' }}>
              <h2 style={{ margin: 0, whiteSpace: 'nowrap', fontSize: '1.6rem' }}>Datos personales</h2>
              <Button type="button" className={notice ? 'button-outline' : ''}
                style={{ ...actionButton, flexShrink: 0, whiteSpace: 'nowrap' }} onClick={startEditing}>
                Editar perfil
              </Button>
            </div>
            <div style={grid}>
              <Item label="Nombre completo">{profile.nombre_completo}</Item>
              <Item label="Correo electrónico">{profile.email}</Item>
              <Item label="DNI">{formatDni(profile.dni)}</Item>
              <Item label="Teléfono">{formatPhone(profile.telefono)}</Item>
              <Item label="Rol">{profile.roles.map((r) => ROLE_LABELS[r] ?? r).join(' y ')}</Item>
            </div>
          </section>

          {isTechnician && (
            <>
              <section className="account-card" style={card}>
              <h2 style={cardTitle}>Especialidades</h2>
                <SpecialtySelector user={user} />
              </section>
              <section className="account-card" style={card}>
                <h2 style={cardTitle}>Resumen profesional</h2>
                <label htmlFor="resumen" style={{ display: 'block', fontWeight: 600, fontSize: '1rem', marginBottom: 8 }}>
                  Cuéntales a los clientes qué haces
                </label>
                <textarea id="resumen" rows={4} disabled
                  placeholder="Qué servicios ofreces, cuántos años de experiencia tienes y en qué zonas trabajas."
                  style={{ width: '100%', padding: 14, borderRadius: 12, border: '1px solid #d1d5db', font: 'inherit', background: '#f9fafb', cursor: 'not-allowed' }} />
                <small style={{ color: '#6b7280' }}>Próximamente: esta sección se habilitará en una próxima entrega.</small>
              </section>

              <section className="account-card" style={card}>
                <h2 style={cardTitle}>Galería de trabajos</h2>
                <div style={{ border: '2px dashed #cbd5e1', borderRadius: 16, padding: '2.5rem 1rem', textAlign: 'center' }}>
                  <ImageIcon size={36} color="#2563eb" />
                  <p style={{ fontWeight: 700, fontSize: '1.1rem', color: 'var(--ink)', margin: '0.75rem 0 0.25rem' }}>Aún no tienes fotos de tus trabajos</p>
                  <p style={{ margin: '0 auto 1.25rem', maxWidth: 440 }}>
                    Súbelas para que los clientes vean lo que sabes hacer. Solo fotos de tus trabajos: los documentos personales, como el DNI, nunca se muestran aquí.
                  </p>
                  <Button type="button" className="button-outline" disabled
                    style={{ ...actionButton, display: 'inline-flex', cursor: 'not-allowed' }}>
                    Agregar fotos
                  </Button>
                  <p style={{ fontSize: '0.8rem', marginTop: 8, marginBottom: 0 }}>Próximamente.</p>
                </div>
              </section>
            </>
          )}
        </div>
      )}

      {profile && editing && (
        <form onSubmit={submit} noValidate className="account-card" style={card}>
          <div style={formGrid}>
            <Field label="Nombre completo" value={profile.nombre_completo} readOnly style={lockedStyle} />
            <Field label="DNI" value={formatDni(profile.dni)} readOnly style={lockedStyle} />
            <div>
              <Field label="Correo electrónico" name="correo" type="email" autoComplete="email"
                defaultValue={profile.email} placeholder="tu@correo.com" required
                aria-invalid={!!errors.correo} style={errors.correo ? { ...inputStyle, ...redBorder } : inputStyle}
                onChange={(e) => validateField('correo', e.currentTarget.value)} />
              <FieldError message={errors.correo} />
            </div>
            <div>
              <Field label="Teléfono" name="telefono" type="tel" autoComplete="tel"
                defaultValue={formatPhone(profile.telefono)} placeholder="9819-7602"
                hint={errors.telefono ? undefined : PHONE_HINT} required
                aria-invalid={!!errors.telefono} style={errors.telefono ? { ...inputStyle, ...redBorder } : inputStyle}
                onChange={(e) => validateField('telefono', e.currentTarget.value)} />
              <FieldError message={errors.telefono} />
            </div>
          </div>

          <p style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.95rem', margin: '0.5rem 0 1.25rem' }}>
            <Lock size={16} /> Nombre y DNI no editables.
          </p>

          <p style={{ fontSize: '1.05rem', color: 'var(--ink)', margin: '0 0 1.75rem' }}>
            <strong>Rol:</strong> {profile.roles.map((r) => ROLE_LABELS[r] ?? r).join(' y ')}
          </p>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
            <Button type="button" className="button-outline" style={actionButton} disabled={busy} onClick={cancelEditing}>Cancelar</Button>
            <Button type="submit" style={actionButton} disabled={busy}>{busy ? 'Guardando…' : 'Guardar cambios'}</Button>
          </div>
        </form>
      )}

      <p style={{ marginTop: '1.5rem' }}>
        <Link to="/mi-cuenta" className="link" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <ArrowLeft size={16} /> Volver a mi cuenta
        </Link>
      </p>
    </main>
  )
}