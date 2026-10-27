import { useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { CheckCircle2, CircleAlert } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import { Button } from './ui'

type Specialty = { id: string; nombre: string }
type Suggestion = { id: string; nombre: string; estado: string }

const ESTADOS: Record<string, string> = {
  PENDIENTE: 'Pendiente de revisión',
  APROBADA: 'Aprobada',
  RECHAZADA: 'Rechazada',
}

export default function SpecialtySelector({ user }: { user: User }) {
  const [catalog, setCatalog] = useState<Specialty[] | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [otherOpen, setOtherOpen] = useState(false)
  const [otherText, setOtherText] = useState('')
  const [otherBusy, setOtherBusy] = useState(false)
  const [otherError, setOtherError] = useState('')
  const [otherNotice, setOtherNotice] = useState('')
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])

  useEffect(() => {
    let active = true
    Promise.all([
      api<Specialty[]>('/specialties', user),
      api<Specialty[]>('/technicians/me/specialties', user),
      api<Suggestion[]>('/technicians/me/specialty-suggestions', user),
    ])
      .then(([all, mine, sugg]) => {
        if (!active) return
        setCatalog(all)
        setSelected(mine.map((s) => s.id))
        setSuggestions(sugg)
      })
      .catch((err) => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [user])

  function toggle(id: string) {
    setNotice('')
    setError('')
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  async function save() {
    if (selected.length === 0) {
      setError('Selecciona al menos una especialidad.')
      return
    }
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const saved = await api<Specialty[]>('/technicians/me/specialties', user, { especialidad_ids: selected }, 'PUT')
      setSelected(saved.map((s) => s.id))
      setNotice('Especialidades guardadas correctamente')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function sendSuggestion() {
    const nombre = otherText.trim()
    if (nombre.length < 3) {
      setOtherError('Escribe el nombre de tu especialidad (mínimo 3 letras).')
      return
    }
    setOtherBusy(true)
    setOtherError('')
    setOtherNotice('')
    try {
      const updated = await api<Suggestion[]>('/technicians/me/specialty-suggestions', user, { nombre })
      setSuggestions(updated)
      setOtherText('')
      setOtherNotice('Enviamos tu sugerencia. Un administrador la revisará antes de agregarla a la lista.')
    } catch (err) {
      setOtherError(errorMessage(err))
    } finally {
      setOtherBusy(false)
    }
  }

  return (
    <div>
      {notice && (
        <div className="success-alert" role="status" style={{ fontSize: '1.1rem', marginBottom: '1.25rem' }}>
          <CheckCircle2 size={22} />
          <span>{notice}</span>
        </div>
      )}
      <p style={{ margin: '0 0 1rem' }}>Elige los oficios en los que trabajas. Puedes marcar varios.</p>

      {!catalog && !error && <p role="status">Cargando especialidades…</p>}

      {catalog && (
        <fieldset style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))' }}>
          <legend style={{ position: 'absolute', left: -9999 }}>Especialidades</legend>
          {catalog.map((s) => {
            const checked = selected.includes(s.id)
            return (
              <label key={s.id} style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '0.9rem 1rem', cursor: 'pointer',
                borderRadius: 12, border: `1.5px solid ${checked ? '#2563eb' : '#d1d5db'}`,
                background: checked ? '#eff6ff' : 'transparent', fontSize: '1.05rem',
              }}>
                <input type="checkbox" checked={checked} onChange={() => toggle(s.id)}
                  style={{ width: 20, height: 20, accentColor: '#2563eb' }} />
                {s.nombre}
              </label>
            )
          })}

          <label style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '0.9rem 1rem', cursor: 'pointer',
            borderRadius: 12, border: `1.5px dashed ${otherOpen ? '#2563eb' : '#d1d5db'}`,
            background: otherOpen ? '#eff6ff' : 'transparent', fontSize: '1.05rem',
          }}>
            <input type="checkbox" checked={otherOpen}
              onChange={() => { setOtherOpen((v) => !v); setOtherError(''); setOtherNotice('') }}
              style={{ width: 20, height: 20, accentColor: '#2563eb' }} />
            Otro
          </label>
        </fieldset>
      )}

      {catalog && otherOpen && (
        <div style={{ marginTop: '1rem', padding: '1rem 1.25rem', border: '1.5px solid #d1d5db', borderRadius: 12 }}>
          <label htmlFor="otra-especialidad" style={{ display: 'block', fontWeight: 600, marginBottom: 8 }}>
            ¿Cuál es tu especialidad?
          </label>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <input id="otra-especialidad" type="text" value={otherText} maxLength={80}
              placeholder="Ej. Instalación de paneles solares"
              onChange={(e) => { setOtherText(e.currentTarget.value); setOtherError('') }}
              style={{ flex: '1 1 240px', minWidth: 0, maxWidth: '100%', minHeight: 48, padding: '0 14px', borderRadius: 12, border: '1px solid #d1d5db', font: 'inherit' }} />
            <Button type="button" style={{ width: 'auto', padding: '0.8rem 1.4rem', fontSize: '1rem' }}
              disabled={otherBusy} onClick={sendSuggestion}>
              {otherBusy ? 'Enviando…' : 'Enviar sugerencia'}
            </Button>
          </div>
          <small style={{ color: '#6b7280', display: 'block', marginTop: 8 }}>
            Un administrador la revisará. Cuando la apruebe, aparecerá en la lista para que la marques.
          </small>
          {otherError && (
            <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6, margin: '0.75rem 0 0' }}>
              <CircleAlert size={16} /> {otherError}
            </p>
          )}
          {otherNotice && (
            <p role="status" style={{ color: '#166534', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6, margin: '0.75rem 0 0' }}>
              <CheckCircle2 size={16} /> {otherNotice}
            </p>
          )}
        </div>
      )}

      {suggestions.length > 0 && (
        <div style={{ marginTop: '1.25rem' }}>
          <p style={{ fontWeight: 600, margin: '0 0 0.5rem' }}>Tus sugerencias</p>
          <ul style={{ margin: 0, paddingLeft: '1.25rem' }}>
            {suggestions.map((s) => (
              <li key={s.id}>{s.nombre} — <span style={{ color: '#6b7280' }}>{ESTADOS[s.estado] ?? s.estado}</span></li>
            ))}
          </ul>
        </div>
      )}

      {error && (
        <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6, margin: '1rem 0 0' }}>
          <CircleAlert size={16} /> {error}
        </p>
      )}

      {catalog && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '1.5rem' }}>
          <Button type="button" style={{ width: 'auto', padding: '0.8rem 1.6rem', fontSize: '1rem' }}
            disabled={busy} onClick={save}>
            {busy ? 'Guardando…' : 'Guardar especialidades'}
          </Button>
        </div>
      )}
    </div>
  )
}
