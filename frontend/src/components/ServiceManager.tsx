import LoadingState from './LoadingState'
import { useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { CheckCircle2, CircleAlert } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import { Button } from './ui'

type Specialty = { id: string; nombre: string }
export type PricingUnit = 'POR_HORA' | 'POR_SERVICIO' | 'POR_DIA' | 'POR_PROYECTO'
export const PRICING_UNIT_LABELS: Record<PricingUnit, string> = {
  POR_HORA: 'Por hora',
  POR_SERVICIO: 'Por servicio',
  POR_DIA: 'Por día',
  POR_PROYECTO: 'Por proyecto',
}
type Service = { id: string; nombre: string; descripcion: string; especialidad: Specialty | null; tarifa_base: number; unidad_cobro: PricingUnit; activo: boolean }

const inputStyle = { width: '100%', minHeight: 48, padding: '0 14px', borderRadius: 12, border: '1px solid #d1d5db', font: 'inherit', background: '#fff' }
const labelStyle = { display: 'block', fontWeight: 600, fontSize: '1rem', marginBottom: 6 }
const smallButton = { width: 'auto', padding: '0.5rem 1rem', fontSize: '0.95rem' }

export default function ServiceManager({ user }: { user: User }) {
  const [services, setServices] = useState<Service[] | null>(null)
  const [specialties, setSpecialties] = useState<Specialty[]>([])
  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [nombre, setNombre] = useState('')
  const [descripcion, setDescripcion] = useState('')
  const [especialidadId, setEspecialidadId] = useState('')
  const [tarifaBase, setTarifaBase] = useState<string>('')
  const [unidadCobro, setUnidadCobro] = useState<PricingUnit>('POR_HORA')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    api<Service[]>('/technicians/me/services', user)
      .then((list) => { if (active) setServices(list) })
      .catch((err) => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [user])

  // Se vuelven a pedir las especialidades al abrir el formulario, por si el técnico acaba de cambiarlas arriba.
  async function openForm(service?: Service) {
    setNotice('')
    setError('')
    setFormError('')
    try {
      const mine = await api<Specialty[]>('/technicians/me/specialties', user)
      setSpecialties(mine)
      setEditingId(service?.id ?? null)
      setNombre(service?.nombre ?? '')
      setDescripcion(service?.descripcion ?? '')
      setEspecialidadId(service?.especialidad?.id ?? (mine.length === 1 ? mine[0].id : ''))
      setTarifaBase(service?.tarifa_base ? String(service.tarifa_base) : '')
      setUnidadCobro(service?.unidad_cobro ?? 'POR_HORA')
      setFormOpen(true)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  function closeForm() {
    setFormOpen(false)
    setEditingId(null)
    setTarifaBase('')
    setUnidadCobro('POR_HORA')
    setFormError('')
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = nombre.trim()
    const description = descripcion.trim()
    const parsedTarifa = parseFloat(tarifaBase)
    if (name.length < 3) return setFormError('El nombre del servicio debe tener al menos 3 caracteres.')
    if (description.length < 10) return setFormError('La descripción debe tener al menos 10 caracteres.')
    if (!especialidadId) return setFormError('Elige la especialidad a la que pertenece el servicio.')
// --- HU-09 ST-04: Validaciones numéricas de tarifa base ---
    if (!tarifaBase || tarifaBase.trim() === '') {
      return setFormError('Por favor ingresa una tarifa base.')
    }
    
    if (isNaN(parsedTarifa)) {
      return setFormError('La tarifa debe ser un número válido.')
    }
    // CA-03: La tarifa debe ser mayor que cero
    if (parsedTarifa <= 0) {
      return setFormError('La tarifa base debe ser mayor que cero (L. 0.00).')
    }
    if (parsedTarifa > 999999.99) {
      return setFormError('La tarifa base no puede exceder L. 999,999.99.')
    }
    // ---------------------------------------------------------
    setBusy(true)
    setFormError('')
    try {
      const body = { nombre: name, descripcion: description, especialidad_id: especialidadId, tarifa_base: parsedTarifa, unidad_cobro: unidadCobro, }
      if (editingId) {
        const updated = await api<Service>(`/technicians/me/services/${editingId}`, user, body, 'PATCH')
        setServices((prev) => (prev ?? []).map((s) => (s.id === updated.id ? updated : s)))
        setNotice('Servicio y tarifa actualizados correctamente')
      } else {
        const created = await api<Service>('/technicians/me/services', user, body)
        setServices((prev) => [created, ...(prev ?? [])])
        setNotice('Servicio y tarifa agregados correctamente')
      }
      closeForm()
    } catch (err) {
      setFormError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function toggleActive(service: Service) {
    setNotice('')
    setError('')
    try {
      const updated = await api<Service>(`/technicians/me/services/${service.id}`, user, { activo: !service.activo }, 'PATCH')
      setServices((prev) => (prev ?? []).map((s) => (s.id === updated.id ? updated : s)))
      setNotice(updated.activo ? 'Servicio activado. Ya se muestra en tu perfil público.' : 'Servicio desactivado. Ya no se muestra a los clientes.')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  if (!services && !error) return <LoadingState message="Cargando tus servicios." />

  return (
    <div>
      {notice && (
        <div className="success-alert" role="status" style={{ marginBottom: '1.25rem' }}>
          <CheckCircle2 size={20} />
          <span>{notice}</span>
        </div>
      )}
      {error && (
        <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6, margin: '0 0 1rem' }}>
          <CircleAlert size={16} /> {error}
        </p>
      )}

      <p style={{ margin: '0 0 1rem' }}>
        Cuéntales a los clientes qué servicios ofreces. Solo los servicios activos se muestran en tu perfil público.
      </p>

      {!formOpen && (
        <Button type="button" style={{ width: 'auto', padding: '0.8rem 1.6rem', fontSize: '1rem', marginBottom: '1.25rem' }}
          onClick={() => openForm()}>
          Agregar servicio
        </Button>
      )}

      {formOpen && (
        <form onSubmit={submit} noValidate style={{ border: '1.5px solid #d1d5db', borderRadius: 12, padding: '1.25rem', marginBottom: '1.5rem' }}>
          <h3 style={{ margin: '0 0 1rem', fontSize: '1.2rem' }}>{editingId ? 'Editar servicio' : 'Nuevo servicio'}</h3>

          {specialties.length === 0 ? (
            <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, margin: 0 }}>
              Primero guarda al menos una especialidad en tu perfil. Cada servicio debe pertenecer a una de ellas.
            </p>
          ) : (
            <>
              <div style={{ marginBottom: '1rem' }}>
                <label htmlFor="servicio-nombre" style={labelStyle}>Nombre del servicio</label>
                <input id="servicio-nombre" type="text" value={nombre} maxLength={100}
                  placeholder="Ej. Instalación de tomacorrientes"
                  onChange={(e) => { setNombre(e.currentTarget.value); setFormError('') }} style={inputStyle} />
              </div>

              <div style={{ marginBottom: '1rem' }}>
                <label htmlFor="servicio-especialidad" style={labelStyle}>Especialidad</label>
                <select id="servicio-especialidad" value={especialidadId}
                  onChange={(e) => { setEspecialidadId(e.currentTarget.value); setFormError('') }} style={inputStyle}>
                  <option value="">Elige una especialidad</option>
                  {specialties.map((s) => <option key={s.id} value={s.id}>{s.nombre}</option>)}
                </select>
                <small style={{ color: '#6b7280', display: 'block', marginTop: 6 }}>
                  ¿No ves tu especialidad? Agrégala en la sección Especialidades, más arriba en tu perfil.
                </small>
              </div>
              {/* HU-09 ST-03: Formulario de precios en Lempiras y Unidad de Cobro */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', marginBottom: '1rem' }}>
                <div>
                  <label htmlFor="servicio-tarifa" style={labelStyle}>Tarifa base (Lempiras)</label>
                  <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                    <span style={{ position: 'absolute', left: 14, fontWeight: 700, color: 'var(--muted)', fontSize: '1rem' }}>
                      L.
                    </span>
                    <input
                      id="servicio-tarifa"
                      type="number"
                      step="0.01"
                      min="0.01"
                      max="999999.99"
                      placeholder="0.00"
                      value={tarifaBase}
                      onKeyDown={(e) => {
                        // Bloquear signos negativos, exponentes y caracteres inválidos
                        if (e.key === '-' || e.key === 'e' || e.key === 'E' || e.key === '+') {
                          e.preventDefault()
                        }
                      }}
                      onChange={(e) => {
                        setTarifaBase(e.currentTarget.value)
                        setFormError('')
                      }}
                      style={{ ...inputStyle, paddingLeft: 38 }}
                      required
                    />
                  </div>
                  <small style={{ color: '#6b7280', display: 'block', marginTop: 4 }}>
                    Precio estimado en Lempiras (L.)
                  </small>
                </div>

                <div>
                  <label htmlFor="servicio-unidad" style={labelStyle}>Unidad de cobro</label>
                  <select
                    id="servicio-unidad"
                    value={unidadCobro}
                    onChange={(e) => setUnidadCobro(e.currentTarget.value as PricingUnit)}
                    style={inputStyle}
                  >
                    <option value="POR_HORA">Por hora</option>
                    <option value="POR_SERVICIO">Por servicio</option>
                    <option value="POR_DIA">Por día</option>
                    <option value="POR_PROYECTO">Por proyecto</option>
                  </select>
                  <small style={{ color: '#6b7280', display: 'block', marginTop: 4 }}>
                    Modalidad de cobro del trabajo
                  </small>
                </div>
              </div>
              
              <div style={{ marginBottom: '1rem' }}>
                <label htmlFor="servicio-descripcion" style={labelStyle}>Descripción</label>
                <textarea id="servicio-descripcion" rows={4} value={descripcion} maxLength={1000}
                  placeholder="Qué incluye el servicio, materiales, tiempos aproximados…"
                  onChange={(e) => { setDescripcion(e.currentTarget.value); setFormError('') }}
                  style={{ ...inputStyle, padding: 14, minHeight: 0 }} />
              </div>
            </>
          )}

          {formError && (
            <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6, margin: '0 0 1rem' }}>
              <CircleAlert size={16} /> {formError}
            </p>
          )}

          <div className="responsive-actions">
            <Button type="button" className="button-outline" style={smallButton} disabled={busy} onClick={closeForm}>Cancelar</Button>
            {specialties.length > 0 && (
              <Button type="submit" style={smallButton} disabled={busy}>
                {busy ? 'Guardando…' : editingId ? 'Guardar cambios' : 'Guardar servicio'}
              </Button>
            )}
          </div>
        </form>
      )}



      {services && services.length === 0 && !formOpen && (
        <p style={{ color: '#6b7280' }}>Aún no has agregado servicios.</p>
      )}

      {services && services.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 12 }}>
          {services.map((s) => (
            <li key={s.id} style={{
              border: '1.5px solid #d1d5db', borderRadius: 12, padding: '1rem 1.25rem',
              background: s.activo ? 'transparent' : '#f9fafb', opacity: s.activo ? 1 : 0.8,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 700, fontSize: '1.1rem' }}>{s.nombre}</span>
                    <span style={{
                      background: '#f0fdf4',
                      border: '1px solid #bbf7d0',
                      color: '#166534',
                      fontWeight: 700,
                      fontSize: '0.9rem',
                      padding: '2px 8px',
                      borderRadius: 6
                    }}>
                      L. {Number(s.tarifa_base || 0).toFixed(2)} &bull; {PRICING_UNIT_LABELS[s.unidad_cobro] ?? s.unidad_cobro}
                    </span>
                  </div>
                  <div style={{ color: '#6b7280', fontSize: '0.9rem', margin: '2px 0 6px' }}>
                    {s.especialidad?.nombre ?? 'Sin especialidad'} · {s.activo ? 'Activo' : 'Desactivado'}
                  </div>
                  <div style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{s.descripcion}</div>
                </div>
                <div className="responsive-actions">
                  <Button type="button" className="button-outline" style={smallButton} onClick={() => openForm(s)}>Editar</Button>
                  <Button type="button" className="button-outline" style={smallButton} onClick={() => toggleActive(s)}>
                    {s.activo ? 'Desactivar' : 'Activar'}
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
