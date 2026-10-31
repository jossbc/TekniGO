import LoadingState from './components/LoadingState'
import { useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { CheckCircle2, CircleAlert, MapPin, Plus, Trash2, X } from 'lucide-react'
import { api, errorMessage } from './lib/api'
import { Button } from './components/ui'
import hondurasLocation from './data/hondurasLocation.json'

export type CoverageZone = {
  departamento: string
  municipio: string
  sector: string
}

const inputStyle = {
  width: '100%',
  minHeight: 46,
  padding: '10px 14px',
  borderRadius: 8,
  border: '1px solid #dce2eb',
  fontSize: '0.95rem',
  background: '#fff'
}
export default function CoverageZoneSelector({ user }: { user: User }) {
  const [zones, setZones] = useState<CoverageZone[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  // Selección vinculada
  const [selectedDepto, setSelectedDepto] = useState<string>('')
  const [selectedMunicipio, setSelectedMunicipio] = useState<string>('')
  const [customSector, setCustomSector] = useState<string>('')
  const [formError, setFormError] = useState('')
  useEffect(() => {
    let active = true
    api<CoverageZone[]>('/technicians/me/coverage-zones', user)
      .then((data) => {
        if (active) {
          setZones(data ?? [])
          setLoading(false)
        }
      })
      .catch((err) => {
        if (active) {
          setError(errorMessage(err))
          setLoading(false)
        }
      })
    return () => { active = false }
  }, [user])
  function handleDeptoChange(depto: string) {
    setSelectedDepto(depto)
    setSelectedMunicipio('')
    setFormError('')
  }
  function handleAddZone(e: FormEvent) {
    e.preventDefault()
    setFormError('')
    setNotice('')
    if (!selectedDepto) {
      setFormError('Por favor selecciona un departamento.')
      return
    }
   
    let finalMunicipio = selectedMunicipio.trim()
    let finalSector = customSector.trim()
    if (!finalMunicipio) {
      setFormError('Por favor selecciona un municipio.')
      return
    }
    if (!finalSector) {
      setFormError('Por favor escribe un sector o colonia.')
      return
    }
    // Regla: Municipio no puede contener números
    if (/\d/.test(finalMunicipio)) {
      setFormError('El municipio no puede contener números.')
      return
    }
    // Regla: Sector no puede comenzar con un número
    if (/^\d/.test(finalSector)) {
      setFormError('El sector no puede comenzar con un número.')
      return
    }
    // Asegurar primera letra mayúscula
    finalMunicipio = finalMunicipio.charAt(0).toUpperCase() + finalMunicipio.slice(1)
    finalSector = finalSector.charAt(0).toUpperCase() + finalSector.slice(1)
    // Validar duplicados exactos
    const exists = zones.some(
      (z) => z.departamento.toLowerCase() === selectedDepto.toLowerCase() &&
             z.municipio.toLowerCase() === finalMunicipio.toLowerCase() &&
             z.sector.toLowerCase() === finalSector.toLowerCase()
    )
    if (exists) {
      setFormError('Esta zona ya está agregada a tu lista.')
      return
    }
    setZones([...zones, { departamento: selectedDepto, municipio: finalMunicipio, sector: finalSector }])
    
    // Limpiar campo de sector para seguir agregando en el mismo municipio
    setCustomSector('')
  }
  function handleRemoveZone(index: number) {
    setNotice('')
    setZones(zones.filter((_, i) => i !== index))
  }
  async function handleSave() {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const saved = await api<CoverageZone[]>(
        '/technicians/me/coverage-zones',
        user,
        { zonas_cobertura: zones },
        'PATCH'
      )
      setZones(saved ?? zones)
      setNotice('Zonas de cobertura actualizadas correctamente.')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  // Lista de departamentos disponibles (claves del JSON)
  const departamentos = Object.keys(hondurasLocation)
  // Municipios correspondientes al departamento seleccionado
  const municipiosDisponibles = selectedDepto
    ? (hondurasLocation as Record<string, string[]>)[selectedDepto] ?? []
    : []
  if (loading) {
    return <LoadingState message="Cargando zonas de cobertura." />
  }
  return (
    <div>
      <p style={{ color: 'var(--muted)', fontSize: '0.95rem', marginTop: 0, marginBottom: '1.25rem' }}>
        Indica los departamentos, municipios y sectores donde trabajas para recibir solicitudes dentro de tu área de atención.
      </p>
      {notice && (
        <div className="success-alert" role="status" style={{ marginBottom: '1.25rem' }}>
          <CheckCircle2 size={20} />
          <span>{notice}</span>
        </div>
      )}
      {error && (
        <div className="alert" role="alert" style={{ marginBottom: '1.25rem' }}>
          <CircleAlert size={18} style={{ display: 'inline', marginRight: 6 }} />
          {error}
        </div>
      )}
      {/* Formulario de selección vinculada */}
      <form onSubmit={handleAddZone} style={{ background: '#f9fafb', padding: '1.25rem', borderRadius: 12, border: '1px solid #e5e7eb', marginBottom: '1.5rem' }}>
        <h4 style={{ margin: '0 0 1rem', fontSize: '1rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
          <MapPin size={18} color="var(--blue)" /> Agregar nueva zona
        </h4>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem', marginBottom: '1rem' }}>
          {/* 1. Departamento */}
          <div>
            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: 4 }}>
              Departamento
            </label>
            <select
              value={selectedDepto}
              onChange={(e) => handleDeptoChange(e.target.value)}
              style={inputStyle}
            >
              <option value="">-- Selecciona un departamento --</option>
              {departamentos.map((depto) => (
                <option key={depto} value={depto}>{depto}</option>
              ))}
            </select>
          </div>
          {/* 2. Municipio (vinculado al departamento) */}
          <div>
            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: 4 }}>
              Municipio
            </label>
            <select
              value={selectedMunicipio}
              onChange={(e) => setSelectedMunicipio(e.target.value)}
              disabled={!selectedDepto}
              style={{ ...inputStyle, background: !selectedDepto ? '#f3f4f6' : '#fff' }}
            >
              <option value="">
                {selectedDepto ? '-- Selecciona un municipio --' : '-- Primero elige un departamento --'}
              </option>
              {municipiosDisponibles.map((mun) => (
                <option key={mun} value={mun}>{mun}</option>
              ))}
            </select>
          </div>
          {/* 3. Sector / Colonia (con validaciones en tiempo real) */}
          <div>
            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: 4 }}>
              Sector / Colonia / Barrio
            </label>
            <input
              type="text"
              placeholder="Ej. Kennedy, Guamilito, Centro..."
              value={customSector}
              onChange={(e) => {
                const sinNumeroInicial = e.target.value.replace(/^\d+/, '')
                const conMayuscula = sinNumeroInicial ? sinNumeroInicial.charAt(0).toUpperCase() + sinNumeroInicial.slice(1) : ''
                setCustomSector(conMayuscula)
              }}
              maxLength={35}
              style={inputStyle}
              required
            />
          </div>
        </div>
        {formError && (
          <p role="alert" style={{ color: '#b91c1c', fontSize: '0.85rem', fontWeight: 600, margin: '0 0 10px' }}>
            {formError}
          </p>
        )}
        <Button
          type="submit"
          className="button-outline"
          style={{ width: 'auto', padding: '0.5rem 1.25rem', fontSize: '0.9rem', display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <Plus size={16} /> Agregar a la lista
        </Button>
      </form>
      {/* Lista de zonas agregadas */}
      <div style={{ marginBottom: '1.5rem' }}>
        <h4 style={{ fontSize: '1rem', fontWeight: 700, margin: '0 0 0.75rem' }}>
          Zonas de cobertura seleccionadas ({zones.length})
        </h4>
        {zones.length === 0 ? (
          <p style={{ color: '#6b7280', fontSize: '0.9rem', fontStyle: 'italic' }}>
            Aún no has agregado ninguna zona de cobertura.
          </p>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.6rem' }}>
            {zones.map((zone, index) => (
              <span
                key={`${zone.municipio}-${zone.sector}-${index}`}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 12px',
                  borderRadius: 20,
                  background: '#eff6ff',
                  border: '1px solid #bfdbfe',
                  color: '#1d4ed8',
                  fontSize: '0.9rem',
                  fontWeight: 500
                }}
              >
                <MapPin size={14} />
                <strong>{zone.departamento}:</strong><strong>{zone.municipio},</strong> {zone.sector}
                <button
                  type="button"
                  onClick={() => handleRemoveZone(index)}
                  title="Eliminar zona"
                  style={{
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    color: '#93c5fd',
                    display: 'flex',
                    alignItems: 'center'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.color = '#ef4444'}
                  onMouseLeave={(e) => e.currentTarget.style.color = '#93c5fd'}
                >
                  <X size={15} />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
      {/* Guardar cambios */}
      <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
        <Button
          type="button"
          onClick={handleSave}
          disabled={busy}
          style={{ width: 'auto', minWidth: 180 }}
        >
          {busy ? 'Guardando…' : 'Guardar zonas de cobertura'}
        </Button>
      </div>
    </div>
  )
}