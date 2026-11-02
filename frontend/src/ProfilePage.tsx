import LoadingState from './components/LoadingState'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { signOut, type User } from 'firebase/auth'
import { ArrowLeft, ArrowUpRight, CheckCircle2, CircleAlert, Clock3, Image as ImageIcon, Lock } from 'lucide-react'
import { Award, ShieldAlert, ShieldCheck, Star } from 'lucide-react'
import { auth } from './lib/firebase'
import { api, errorMessage, type Profile } from './lib/api'
import { Button, Field } from './components/ui'
import SpecialtySelector from './components/SpecialtySelector'
import ServiceManager from './components/ServiceManager'
import CoverageZoneSelector from './CoverageZoneSelector'
import { normalizeEmail, normalizeHonduranPhone, PHONE_HINT } from './lib/validation'

type ProfileChanges = { telefono?: string; correo?: string }
type Errors = { telefono?: string; correo?: string }

export type TechnicianRequirement = {
  codigo: string
  nombre: string
  aprobado: boolean
  vigente: boolean
  estado: 'APROBADO' | 'PENDIENTE' | 'FALTANTE' | 'RECHAZADO' | 'VENCIDO' | 'INVALIDADO' | string
  fecha_emision?: string | null
  fecha_vencimiento?: string | null
  motivo?: string | null
}

export type TechnicianActivationStatus = {
  usuario_id: string
  estado_verificacion: string
  tecnico_verificado: boolean
  telefono_verificado: boolean
  disponible: boolean
  suspendido: boolean
  restriccion_financiera: boolean
  estado_certificacion: string
  calificacion_promedio: number | null
  puede_recibir_contrataciones: boolean
  habilitado_operativamente: boolean
  motivo_restriccion?: string | null
  observaciones_verificacion?: string | null
  requisitos: TechnicianRequirement[]
  requisitos_pendientes: TechnicianRequirement[]
  motivos_bloqueo: string[]
}

const ROLE_LABELS: Record<string, string> = { CLIENTE: 'Cliente', TECNICO: 'Técnico' }
const IDENTITY_STATUS_LABELS: Record<string, string> = {
  PENDIENTE: 'Pendiente de verificación',
  EN_REVISION: 'En revisión administrativa',
  VERIFICADO: 'Verificado',
  RECHAZADO: 'Rechazado (requiere reenvío)',
}
const TECH_VERIFICATION_LABELS: Record<string, string> = {
  NO_VERIFICADO: 'Documentación pendiente',
  PENDIENTE: 'Pendiente de completar requisitos',
  EN_REVISION: 'En revisión administrativa',
  VERIFICADO: 'Técnico Verificado',
  RECHAZADO: 'Rechazado (requiere corrección)',
  RESTRINGIDO: 'Restringido por requisito vencido o inválido',
}
const CERTIFICATION_LABELS: Record<string, string> = {
  NO_CERTIFICADO: 'Sin certificación registrada',
  EN_REVISION: 'Certificación en revisión',
  CERTIFICADO: 'Técnico Certificado',
}

const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))', gap: '2.25rem 3rem' }

const formGrid = { ...grid, gap: '0 3rem' }
const cardTitle = { margin: '0 0 1.25rem', fontSize: '1.6rem' }
const inputStyle = { fontSize: '1rem', minHeight: 52 }
const lockedStyle = { ...inputStyle, background: '#f3f4f6', color: '#6b7280', cursor: 'not-allowed' }
const redBorder = { borderColor: '#b91c1c' }

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
  const [identityStatus, setIdentityStatus] = useState<string>('PENDIENTE')
  const [techActivation, setTechActivation] = useState<TechnicianActivationStatus | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [errors, setErrors] = useState<Errors>({})

  useEffect(() => {
    let active = true
    Promise.all([
      api<Profile>('/users/me/personal', user),
      api<{ estado_identidad: string; documentos: unknown[] }>('/users/me/identity-documents', user).catch(() => null),
    ])
      .then(([value, docsInfo]) => {
        if (!active) return
        setProfile(value)
        setIdentityStatus(docsInfo ? docsInfo.estado_identidad : value.estado_identidad)
        onName?.(value.nombre_completo)
        if (value.roles.includes('TECNICO')) {
          api<TechnicianActivationStatus>('/technicians/me/activation-status', user)
            .then((activation) => { if (active) setTechActivation(activation) })
            .catch(() => {})
        }
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
      if (changes.telefono && updated.roles.includes('TECNICO')) {
        api<TechnicianActivationStatus>('/technicians/me/activation-status', user)
          .then(setTechActivation)
          .catch(() => {})
      }
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

  if (!profile && !error) return <main className="account"><LoadingState message="Cargando tu perfil." /></main>

  const isTechnician = !!profile?.roles.includes('TECNICO')

  return (
    <main className="account profile-page" style={{ margin: '0 auto 90px' }}>
      {notice && (
        <div className="success-alert" role="status">
          <CheckCircle2 size={20} />
          <span>{notice}</span>
        </div>
      )}
      {error && <div className="alert" role="alert">{error}</div>}

      <h1 style={{ marginTop: 0 }}>{editing ? 'Editar perfil' : 'Mi perfil'}</h1>
      {!notice && <p>{editing ? 'Puedes cambiar tu correo y tu teléfono.' : 'Revisa los datos que tienes registrados en TekniGO.'}</p>}



      {profile && !editing && (
        <div style={{ display: 'grid', gap: '1.5rem' }}>
          <section className="account-card profile-card">
            <div className="profile-card-header">
              <h2>Datos personales</h2>
              <Button type="button" className={`profile-action ${notice ? 'button-outline' : ''}`} onClick={startEditing}>
                Editar perfil
              </Button>
            </div>
            <div style={grid}>
              <Item label="Nombre completo">{profile.nombre_completo}</Item>
              <Item label="Correo electrónico">{profile.email}</Item>
              <Item label="DNI">{formatDni(profile.dni)}</Item>
              <Item label="Teléfono">{formatPhone(profile.telefono)}</Item>
              <Item label="Rol">{profile.roles.map((r) => ROLE_LABELS[r] ?? r).join(' y ')}</Item>
              <Item label="Verificación de identidad">
                <span className="status-pill" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: '0.95rem' }}>
                  {identityStatus === 'VERIFICADO' ? <CheckCircle2 size={15} /> : <Clock3 size={15} />}
                  {IDENTITY_STATUS_LABELS[identityStatus] ?? identityStatus}
                </span>
                {identityStatus === 'PENDIENTE' && (
                  <div style={{ marginTop: 8, fontSize: '0.95rem' }}>
                    <Link to="/verificacion" className="link" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      Subir DNI y selfie <ArrowUpRight size={15} />
                    </Link>
                  </div>
                )}
              </Item>
            </div>
          </section>

          {isTechnician && (
            <>
              <section className="account-card profile-card">
                <h2 style={cardTitle}>Estado de activación y verificación técnica</h2>

                {/* CA-03: Mostrar Técnico Verificado separado de certificación y calificación */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem', marginBottom: '1.25rem' }}>
                  <div style={{
                    padding: '1rem',
                    borderRadius: 12,
                    background: techActivation?.tecnico_verificado ? '#f0fdf4' : techActivation?.estado_verificacion === 'RESTRINGIDO' || techActivation?.estado_verificacion === 'RECHAZADO' ? '#fef2f2' : '#eff6ff',
                    border: `1px solid ${techActivation?.tecnico_verificado ? '#86efac' : techActivation?.estado_verificacion === 'RESTRINGIDO' || techActivation?.estado_verificacion === 'RECHAZADO' ? '#fca5a5' : '#93c5fd'}`,
                  }}>
                    <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#4b5563', marginBottom: 4 }}>Estado del perfil</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, fontSize: '1.05rem', color: techActivation?.tecnico_verificado ? '#166534' : '#1e293b' }}>
                      {techActivation?.tecnico_verificado ? <ShieldCheck size={20} color="#16a34a" /> : techActivation?.estado_verificacion === 'RESTRINGIDO' || techActivation?.estado_verificacion === 'RECHAZADO' ? <ShieldAlert size={20} color="#dc2626" /> : <Clock3 size={20} color="#2563eb" />}
                      <span>{TECH_VERIFICATION_LABELS[techActivation?.estado_verificacion ?? 'NO_VERIFICADO'] ?? techActivation?.estado_verificacion}</span>
                    </div>
                  </div>

                  <div style={{ padding: '1rem', borderRadius: 12, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
                    <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#4b5563', marginBottom: 4 }}>Certificación</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, fontSize: '1.05rem', color: '#1e293b' }}>
                      <Award size={20} color="#2563eb" />
                      <span>{CERTIFICATION_LABELS[techActivation?.estado_certificacion ?? 'NO_CERTIFICADO'] ?? techActivation?.estado_certificacion}</span>
                    </div>
                  </div>

                  <div style={{ padding: '1rem', borderRadius: 12, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
                    <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#4b5563', marginBottom: 4 }}>Calificación</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, fontSize: '1.05rem', color: '#1e293b' }}>
                      <Star size={20} color="#eab308" />
                      <span>
                        {techActivation?.calificacion_promedio != null
                          ? `${techActivation.calificacion_promedio.toFixed(1)} / 5.0`
                          : 'Sin calificaciones aún'}
                      </span>
                    </div>
                  </div>
                </div>

                {/* CA-02, CA-04, CA-05: Estado operativo para recibir contrataciones y motivo de restricción */}
                {techActivation && (
                  <div style={{
                    padding: '1rem 1.25rem',
                    borderRadius: 12,
                    background: techActivation.habilitado_operativamente ? '#f0fdf4' : '#fff7ed',
                    border: `1px solid ${techActivation.habilitado_operativamente ? '#86efac' : '#fed7aa'}`,
                    marginBottom: '1.25rem',
                  }}>
                    <div style={{ fontWeight: 700, color: techActivation.habilitado_operativamente ? '#166534' : '#9a3412', marginBottom: 4 }}>
                      {techActivation.habilitado_operativamente
                        ? 'Habilitado para recibir nuevas contrataciones y aparecer en el catálogo'
                        : 'Restringido para nuevas contrataciones'}
                    </div>
                    {!techActivation.habilitado_operativamente && techActivation.motivos_bloqueo.length > 0 && (
                      <ul style={{ margin: '6px 0 0', paddingLeft: '1.25rem', color: '#9a3412', fontSize: '0.92rem' }}>
                        {techActivation.motivos_bloqueo.map((motivo, idx) => (
                          <li key={idx}>{motivo}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}

                {/* ST-04: Lista de requisitos obligatorios y pendientes */}
                {techActivation && techActivation.requisitos.length > 0 && (
                  <div>
                    <h3 style={{ fontSize: '1.05rem', margin: '0 0 0.75rem' }}>
                      Requisitos obligatorios de activación ({techActivation.requisitos.length - techActivation.requisitos_pendientes.length}/{techActivation.requisitos.length} aprobados y vigentes)
                    </h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '0.75rem' }}>
                      {techActivation.requisitos.map((req: TechnicianRequirement) => {
                        const ok = req.aprobado && req.vigente
                        return (
                          <div
                            key={req.codigo}
                            style={{
                              padding: '0.75rem 1rem',
                              borderRadius: 10,
                              border: `1px solid ${ok ? '#bbf7d0' : '#e5e7eb'}`,
                              background: ok ? '#f0fdf4' : '#ffffff',
                              display: 'flex',
                              flexDirection: 'column',
                              gap: 4,
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                              <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{req.nombre}</span>
                              <span style={{
                                fontSize: '0.75rem',
                                fontWeight: 700,
                                padding: '2px 8px',
                                borderRadius: 999,
                                background: ok ? '#dcfce7' : req.estado === 'VENCIDO' || req.estado === 'RECHAZADO' || req.estado === 'INVALIDADO' ? '#fee2e2' : '#fef3c7',
                                color: ok ? '#166534' : req.estado === 'VENCIDO' || req.estado === 'RECHAZADO' || req.estado === 'INVALIDADO' ? '#991b1b' : '#92400e',
                              }}>
                                {req.estado}
                              </span>
                            </div>
                            {req.motivo && (
                              <span style={{ fontSize: '0.82rem', color: '#6b7280' }}>{req.motivo}</span>
                            )}
                            {req.fecha_vencimiento && (
                              <span style={{ fontSize: '0.78rem', color: '#4b5563' }}>
                                Vigencia hasta: {req.fecha_vencimiento}
                              </span>
                            )}
                          </div>
                        )
                      })}
                    </div>

                    {techActivation.estado_verificacion !== 'VERIFICADO' && (
                      <div style={{ marginTop: '1rem' }}>
                        <Link to="/mi-cuenta" className="link" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                          Gestionar documentos de verificación en Mi cuenta <ArrowUpRight size={16} />
                        </Link>
                      </div>
                    )}
                  </div>
                )}
              </section>

              <section className="account-card profile-card">
                <h2 style={cardTitle}>Especialidades</h2>
                <SpecialtySelector user={user} />
              </section>
              <section className="account-card profile-card">
                <h2 style={cardTitle}>Resumen profesional</h2>
                <label htmlFor="resumen" style={{ display: 'block', fontWeight: 600, fontSize: '1rem', marginBottom: 8 }}>
                  Cuéntales a los clientes qué haces
                </label>
                <textarea id="resumen" rows={4} disabled
                  placeholder="Qué servicios ofreces, cuántos años de experiencia tienes y en qué zonas trabajas."
                  style={{ width: '100%', padding: 14, borderRadius: 12, border: '1px solid #d1d5db', font: 'inherit', background: '#f9fafb', cursor: 'not-allowed' }} />
                <small style={{ color: '#6b7280' }}>Próximamente: esta sección se habilitará en una próxima entrega.</small>
              </section>

              <section className="account-card profile-card">
                <h2 style={cardTitle}>Galería de trabajos</h2>
                <div style={{ border: '2px dashed #cbd5e1', borderRadius: 16, padding: '2.5rem 1rem', textAlign: 'center' }}>
                  <ImageIcon size={36} color="#2563eb" />
                  <p style={{ fontWeight: 700, fontSize: '1.1rem', color: 'var(--ink)', margin: '0.75rem 0 0.25rem' }}>Aún no tienes fotos de tus trabajos</p>
                  <p style={{ margin: '0 auto 1.25rem', maxWidth: 440 }}>
                    Súbelas para que los clientes vean lo que sabes hacer. Solo fotos de tus trabajos: los documentos personales, como el DNI, nunca se muestran aquí.
                  </p>
                  <Button type="button" className="button-outline profile-action" disabled
                    style={{ display: 'inline-flex', cursor: 'not-allowed' }}>
                    Agregar fotos
                  </Button>
                  <p style={{ fontSize: '0.8rem', marginTop: 8, marginBottom: 0 }}>Próximamente.</p>
                </div>
              </section>
              <section className="account-card profile-card">
                <h2 style={cardTitle}>Mis servicios</h2>
                <ServiceManager user={user} />
              </section>
                            <section className="account-card profile-card">
                <h2 style={cardTitle}>Zonas de cobertura</h2>
                <CoverageZoneSelector user={user} />
              </section>
            </>
          )}
        </div>
      )}

      {profile && editing && (
        <form onSubmit={submit} noValidate className="account-card profile-card">
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

          <div className="responsive-actions">
            <Button type="button" className="button-outline profile-action" disabled={busy} onClick={cancelEditing}>Cancelar</Button>
            <Button type="submit" className="profile-action" disabled={busy}>{busy ? 'Guardando…' : 'Guardar cambios'}</Button>
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
