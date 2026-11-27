import { useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { api, errorMessage } from '../lib/api'
import { Button } from './ui'

const path = '/admin/catalog'
const fields = [
  { key: 'comision_porcentaje', label: 'Comisión (%)', min: 0, max: 100 },
  { key: 'limite_deuda', label: 'Límite de deuda (L)', min: 0.01, max: 99999999.99 },
  { key: 'minimo_retiro', label: 'Mínimo de retiro (L)', min: 0.01, max: 99999999.99 },
] as const

type Category = { id: string; nombre: string; activa: boolean }
type PendingSuggestion = { id: string; nombre: string }
type Settings = Record<typeof fields[number]['key'], string | null> & {
  limite_sin_liquidar: number
  valores_demo: boolean
}
type Catalog = {
  categorias: Category[]
  unidades: string[]
  tarifas: { especialidad_id: string; unidad: string; promedio: string }[]
  configuracion: Settings
  demo_habilitada: boolean
  sugerencias: PendingSuggestion[]
}
type Change = {
  id: number
  recurso: string
  anterior: Record<string, unknown> | null
  nuevo: Record<string, unknown>
  administrador: string
  fecha: string
}

const auditLabels: Record<string, string> = {
  nombre: 'Nombre', activa: 'Disponible', estado: 'Estado', promedio: 'Promedio sugerido (L)',
  limite_sin_liquidar: 'Límite sin liquidar', valores_demo: 'Valores de prueba',
  ...Object.fromEntries(fields.map(({ key, label }) => [key, label])),
}

function AuditValues({ values }: { values: Record<string, unknown> | null }) {
  if (!values) return <p>Sin valor previo</p>
  return <ul>{Object.entries(values).filter(([key]) => key in auditLabels).map(([key, value]) => (
    <li key={key}>{auditLabels[key]}: {typeof value === 'boolean' ? (value ? 'Sí' : 'No') : String(value ?? 'Sin configurar')}</li>
  ))}</ul>
}

function changeLabel(change: Change, categories: Category[]) {
  const [kind, id, unit] = change.recurso.split(':')
  if (kind === 'categoria') return `Categoría · ${change.nuevo.nombre}`
  if (kind === 'sugerencia') return `Sugerencia · ${change.nuevo.nombre}`
  if (kind === 'tarifa') return `Tarifa · ${categories.find(item => item.id === id)?.nombre ?? id} · ${unit.toLowerCase().replaceAll('_', ' ')}`
  return 'Configuración operativa'
}

export default function AdminCatalog({ user }: { user: User }) {
  const [catalog, setCatalog] = useState<Catalog>()
  const [history, setHistory] = useState<Change[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [categoryId, setCategoryId] = useState('')
  const [tariffId, setTariffId] = useState('')
  const [unit, setUnit] = useState('')
  const category = catalog?.categorias.find(item => item.id === categoryId)
  const tariff = catalog?.tarifas.find(item => item.especialidad_id === tariffId && item.unidad === unit)

  useEffect(() => {
    let active = true
    Promise.all([api<Catalog>(path, user), api<Change[]>(`${path}/history`, user)])
      .then(([snapshot, changes]) => {
        if (active) { setCatalog(snapshot); setHistory(changes) }
      })
      .catch(err => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [user])

  async function save(endpoint: string, method: 'POST' | 'PUT', body?: object) {
    if (busy) return
    setError('')
    setNotice('')
    setBusy(true)
    try {
      await api(`${path}${endpoint}`, user, body, method)
      setNotice('Cambios guardados.')
      const [snapshot, changes] = await Promise.all([api<Catalog>(path, user), api<Change[]>(`${path}/history`, user)])
      setCatalog(snapshot)
      setHistory(changes)
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  function saveCategory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    void save(categoryId ? `/categories/${categoryId}` : '/categories', categoryId ? 'PUT' : 'POST', {
      nombre: data.get('nombre'),
      activa: data.has('activa'),
    })
  }

  return (
    <section style={{ display: "grid", gap: 16 }} aria-label="Administrar categorías y parámetros">
      {error && <p className="alert" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!catalog && !error && <p role="status">Cargando catálogo…</p>}
      {catalog && <>
        <section className="client-request-box">
          <h2>Categorías</h2>
          <label className="field">Categoría
            <select value={categoryId} onChange={event => setCategoryId(event.target.value)}>
              <option value="">Nueva categoría</option>
              {catalog.categorias.map(item => <option key={item.id} value={item.id}>
                {item.nombre}{item.activa ? '' : ' (desactivada)'}
              </option>)}
            </select>
          </label>
          <form key={`${categoryId}:${category?.nombre}:${category?.activa}`} onSubmit={saveCategory}>
            <label className="field">Nombre
              <input name="nombre" required maxLength={80} defaultValue={category?.nombre} />
            </label>
            <label><input type="checkbox" name="activa" defaultChecked={category?.activa ?? true} /> Disponible para nuevas contrataciones</label>
            <Button type="submit" disabled={busy}>{category ? 'Guardar categoría' : 'Crear categoría'}</Button>
          </form>
        </section>
        <section className="client-request-box">
          <h2>Sugerencias de oficios</h2>
          {catalog.sugerencias.length === 0 ? <p>Sin sugerencias pendientes de revisión.</p> : (
            <ul className="catalog-suggestions">
              {catalog.sugerencias.map(suggestion => <li key={suggestion.id}>
                <strong>{suggestion.nombre}</strong>
                <div className="responsive-actions">
                  <Button type="button" disabled={busy} aria-label={`Aprobar ${suggestion.nombre}`}
                    onClick={() => void save(`/suggestions/${suggestion.id}`, 'PUT', { estado: 'APROBADA' })}>
                    Aprobar
                  </Button>
                  <Button type="button" className="button-outline" disabled={busy} aria-label={`Rechazar ${suggestion.nombre}`}
                    onClick={() => void save(`/suggestions/${suggestion.id}`, 'PUT', { estado: 'RECHAZADA' })}>
                    Rechazar
                  </Button>
                </div>
              </li>)}
            </ul>
          )}
        </section>
        <section className="client-request-box">
          <h2>Tarifas promedio sugeridas</h2>
          <label className="field">Categoría
            <select value={tariffId} onChange={event => setTariffId(event.target.value)}>
              <option value="">Elige una categoría</option>
              {catalog.categorias.map(item => <option key={item.id} value={item.id}>{item.nombre}</option>)}
            </select>
          </label>
          <label className="field">Unidad de cobro
            <select value={unit} onChange={event => setUnit(event.target.value)}>
              <option value="">Elige una unidad</option>
              {catalog.unidades.map(item => <option key={item} value={item}>{item.toLowerCase().replaceAll('_', ' ')}</option>)}
            </select>
          </label>
          <form key={`${tariffId}:${unit}:${tariff?.promedio}`} onSubmit={event => {
            event.preventDefault()
            if (tariffId && unit) void save(`/categories/${tariffId}/tariffs/${unit}`, 'PUT', { promedio: new FormData(event.currentTarget).get('promedio') })
          }}>
            <label className="field">Promedio sugerido (L)
              <input type="number" name="promedio" min="0.01" max="99999999.99" step="0.01" required defaultValue={tariff?.promedio} />
            </label>
            <Button type="submit" disabled={busy || !tariffId || !unit}>Guardar tarifa</Button>
          </form>
        </section>
        <section className="client-request-box">
          <h2>Configuración operativa</h2>
          <p>Límite de servicios consecutivos sin liquidar: {catalog.configuracion.limite_sin_liquidar}.</p>
          {catalog.configuracion.valores_demo && <p role="status">Valores de prueba del entorno de demostración.</p>}
          <form key={JSON.stringify(catalog.configuracion)} onSubmit={event => {
            event.preventDefault()
            const values = new FormData(event.currentTarget)
            void save('/settings', 'PUT', Object.fromEntries(fields.map(({ key }) => [key, values.get(key)])))
          }}>
            {fields.map(({ key, label, min, max }) => <label className="field" key={key}>{label}
              <input type="number" name={key} required min={min} max={max} step="0.01" defaultValue={catalog.configuracion[key] ?? ''} />
            </label>)}
            <Button type="submit" disabled={busy}>Guardar configuración</Button>
          </form>
          {catalog.demo_habilitada && fields.every(({ key }) => catalog.configuracion[key] === null) &&
            <Button type="button" disabled={busy} onClick={() => void save('/settings/demo', 'POST')}>
              Cargar ejemplos de prueba (10 %, L 500, L 200)
            </Button>}
        </section>
        <section className="client-request-box">
          <h2>Historial de cambios</h2>
          {history.length === 0 && <p>Todavía no hay cambios registrados.</p>}
          {history.map(change => <details key={change.id}>
            <summary>{changeLabel(change, catalog.categorias)} · {new Date(change.fecha).toLocaleString('es-HN')}</summary>
            <p>Administrador: {change.administrador}</p>
            <dl>
              <dt>Antes</dt><dd><AuditValues values={change.anterior} /></dd>
              <dt>Después</dt><dd><AuditValues values={change.nuevo} /></dd>
            </dl>
          </details>)}
        </section>
      </>}
    </section>
  )
}
