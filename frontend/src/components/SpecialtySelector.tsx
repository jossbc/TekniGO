import { useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { CheckCircle2, CircleAlert } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import { Button } from './ui'

type Specialty = { id: string; nombre: string }

export default function SpecialtySelector({ user }: { user: User }) {
  const [catalog, setCatalog] = useState<Specialty[] | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([
      api<Specialty[]>('/specialties', user),
      api<Specialty[]>('/technicians/me/specialties', user),
    ])
      .then(([all, mine]) => {
        if (!active) return
        setCatalog(all)
        setSelected(mine.map((s) => s.id))
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
        </fieldset>
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