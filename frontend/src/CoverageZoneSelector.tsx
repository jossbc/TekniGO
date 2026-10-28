import { useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { CheckCircle2, CircleAlert, MapPin, Plus, Trash2, X } from 'lucide-react'
import { api, errorMessage } from './lib/api'
import { Button } from './components/ui'

export type CoverageZone = {
  municipio: string
  sector: string
}

// Catálogo base de municipios y sectores en Honduras
const HONDURAS_LOCATIONS: Record<string, string[]> = {
  'Distrito Central (Tegucigalpa / M.D.C.)': [
    'Kennedy', 'Miraflores', 'El Hatillo', 'Palmira', 'Las Colinas',
    'Loarque', 'Cerro Grande', 'Florencia', 'Centro Histórico',
    'Hato de Enmedio', 'Valle de Ángeles', 'Santa Lucía', 'Otro sector...'
  ],
  'San Pedro Sula': [
    'Nor-Oeste', 'Nor-Este', 'Sur-Oeste', 'Sur-Este', 'Barrio Guamilito',
    'Chamelecón', 'Rivera Hernández', 'Cofradía', 'Satélite', 'Jardines del Valle', 'Otro sector...'
  ],
  'La Ceiba': [
    'El Centro', 'Barrio Inglés', 'Miramar', 'Mazapán', 'Buenos Aires', 'Potreritos', 'Otro sector...'
  ],
  'Choloma': [
    'Centro', 'López Arellano', 'Cerro Verde', 'Quebrada Seca', 'Otro sector...'
  ],
  'El Progreso': [
    'Centro', 'San José', 'Bendeck', 'Palermo', 'Otro sector...'
  ],
  'Comayagua': [
    'Centro', 'Torocagua', 'Cabañas', 'La Joya', 'Otro sector...'
  ],
  'Danlí': [
    'El Centro', 'Gualiqueme', 'La Concepción', 'Otro sector...'
  ],
  'Villanueva': [
    'Centro', 'Dos Caminos', 'El Calán', 'Otro sector...'
  ],
  'Otro municipio...': []
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

  // Formulario para agregar una nueva zona
  const [selectedMunicipio, setSelectedMunicipio] = useState<string>('')
  const [customMunicipio, setCustomMunicipio] = useState<string>('')
  const [selectedSector, setSelectedSector] = useState<string>('')
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

  function handleMunicipioChange(value: string) {
    setSelectedMunicipio(value)
    setSelectedSector('')
    setCustomMunicipio('')
    setCustomSector('')
    setFormError('')
  }

  function handleAddZone(e: FormEvent) {
    e.preventDefault()
    setFormError('')
    setNotice('')

    let finalMunicipio = (selectedMunicipio === 'Otro municipio...' ? customMunicipio : selectedMunicipio).trim()
    let finalSector = (
      selectedMunicipio === 'Otro municipio...' || selectedSector === 'Otro sector...'
        ? customSector
        : selectedSector
    ).trim()

    if (!finalMunicipio) {
      setFormError('Por favor selecciona o escribe un municipio.')
      return
    }
    if (!finalSector) {
      setFormError('Por favor selecciona o escribe un sector.')
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

    // Validar duplicados en la lista local
    const exists = zones.some(
      (z) => z.municipio.toLowerCase() === finalMunicipio.toLowerCase() &&
             z.sector.toLowerCase() === finalSector.toLowerCase()
    )

    if (exists) {
      setFormError('Esta zona ya está agregada a tu lista.')
      return
    }

    setZones([...zones, { municipio: finalMunicipio, sector: finalSector }])
    
    // Limpiar campos del selector
    if (selectedMunicipio !== 'Otro municipio...') {
      setSelectedSector('')
      setCustomSector('')
    } else {
      setSelectedMunicipio('')
      setSelectedSector('')
      setCustomMunicipio('')
      setCustomSector('')
    }
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

  const availableSectors = selectedMunicipio && selectedMunicipio !== 'Otro municipio...'
    ? (HONDURAS_LOCATIONS[selectedMunicipio] ?? [])
    : []

  if (loading) {
    return <p style={{ color: 'var(--muted)' }}>Cargando zonas de cobertura…</p>
  }

  return (
    <div>
      <p style={{ color: 'var(--muted)', fontSize: '0.95rem', marginTop: 0, marginBottom: '1.25rem' }}>
        Indica los municipios y sectores donde puedes ofrecer tus servicios para recibir solicitudes en tu área de cobertura.
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

      {/* Formulario para añadir zona */}
      <form onSubmit={handleAddZone} style={{ background: '#f9fafb', padding: '1.25rem', borderRadius: 12, border: '1px solid #e5e7eb', marginBottom: '1.5rem' }}>
        <h4 style={{ margin: '0 0 1rem', fontSize: '1rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
          <MapPin size={18} color="var(--blue)" /> Agregar nueva zona
        </h4>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem', marginBottom: '1rem' }}>
          {/* Municipio */}
          <div>
            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: 4 }}>
              Municipio
            </label>
            <select
              value={selectedMunicipio}
              onChange={(e) => handleMunicipioChange(e.target.value)}
              style={inputStyle}
            >
              <option value="">-- Selecciona un municipio --</option>
              {Object.keys(HONDURAS_LOCATIONS).map((mun) => (
                <option key={mun} value={mun}>{mun}</option>
              ))}
            </select>
                        {selectedMunicipio === 'Otro municipio...' && (
              <input
                type="text"
                placeholder="Escribe el nombre del municipio"
                value={customMunicipio}
                onChange={(e) => {
                  // Eliminar cualquier número y poner primera letra mayúscula
                  const sinNumeros = e.target.value.replace(/[0-9]/g, '')
                  const conMayuscula = sinNumeros ? sinNumeros.charAt(0).toUpperCase() + sinNumeros.slice(1) : ''
                  setCustomMunicipio(conMayuscula)
                }}
                maxLength={35}
                style={{ ...inputStyle, marginTop: 8 }}
                required
              />
            )}
          </div>

          {/* Sector */}
          <div>
            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: 4 }}>
              Sector / Colonia / Barrio
            </label>
                        {selectedMunicipio === 'Otro municipio...' ? (
              <input
                type="text"
                placeholder="Escribe el sector o colonia"
                value={customSector}
                onChange={(e) => {
                  // No permitir que empiece con números y asegurar primera letra mayúscula
                  const sinNumeroInicial = e.target.value.replace(/^\d+/, '')
                  const conMayuscula = sinNumeroInicial ? sinNumeroInicial.charAt(0).toUpperCase() + sinNumeroInicial.slice(1) : ''
                  setCustomSector(conMayuscula)
                }}
                maxLength={35}
                style={inputStyle}
                required
              />
            ) : (
              <>
                <select
                  value={selectedSector}
                  onChange={(e) => setSelectedSector(e.target.value)}
                  disabled={!selectedMunicipio}
                  style={{ ...inputStyle, background: !selectedMunicipio ? '#f3f4f6' : '#fff' }}
                >
                  <option value="">-- Selecciona un sector --</option>
                  {availableSectors.map((sec) => (
                    <option key={sec} value={sec}>{sec}</option>
                  ))}
                </select>
                                {selectedSector === 'Otro sector...' && (
                  <input
                    type="text"
                    placeholder="Escribe el nombre del sector"
                    value={customSector}
                    onChange={(e) => {
                      // No permitir que empiece con números y asegurar primera letra mayúscula
                      const sinNumeroInicial = e.target.value.replace(/^\d+/, '')
                      const conMayuscula = sinNumeroInicial ? sinNumeroInicial.charAt(0).toUpperCase() + sinNumeroInicial.slice(1) : ''
                      setCustomSector(conMayuscula)
                    }}
                    maxLength={35}
                    style={{ ...inputStyle, marginTop: 8 }}
                    required
                  />
                )}
              </>
            )}
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
          Zonas seleccionadas ({zones.length})
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
                <strong>{zone.municipio}:</strong> {zone.sector}
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