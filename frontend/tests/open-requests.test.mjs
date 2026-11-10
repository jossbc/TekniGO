import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter } = require('react-router-dom')

let mockSuggestedRange = null
let mockCreated = null
let mockPresupuesto = ''

const result = await build({
  stdin: {
    contents: `
      export { default as OpenRequestForm } from './src/components/OpenRequestForm'
      export * from './src/lib/openRequests'
    `,
    resolveDir: process.cwd(),
    loader: 'tsx',
  },
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.css': 'empty' },
  define: { 'import.meta.env': '{}' },
})

const module = { exports: {} }
let stateIdx = 0
new Function('require', 'module', 'exports', result.outputFiles[0].text)((id) => {
  if (id === 'react') {
    return {
      ...React,
      useEffect() {},
      useState(initial) {
        const idx = stateIdx++
        // State order in OpenRequestForm:
        // 0: specialties, 1: eligibility, 2: loadingInit, 3: especialidadId, 4: descripcion,
        // 5: municipio, 6: sector, 7: direccionExacta, 8: fechaHora, 9: metodoPago, 10: presupuesto,
        // 11: suggestedRange, 12: loadingRange, 13: submitting, 14: error, 15: created
        if (idx === 10) return [mockPresupuesto, () => {}]
        if (idx === 11) return [mockSuggestedRange, () => {}]
        if (idx === 12) return [false, () => {}]
        if (idx === 15) return [mockCreated, () => {}]
        return [initial, () => {}]
      },
    }
  }
  return require(id)
}, module, module.exports)

const sampleSpecialties = [{ id: 'spec-plomeria', nombre: 'Plomería' }]
const enabledEligibility = {
  puede_contratar: true,
  identidad_verificada: true,
  correo_verificado: true,
  telefono_verificado: true,
  motivos: [],
}

function renderForm(eligibility = enabledEligibility) {
  stateIdx = 0
  return renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      {},
      React.createElement(module.exports.OpenRequestForm, {
        user: { uid: 'client-1', email: 'ana@example.com' },
        initialSpecialties: sampleSpecialties,
        initialEligibility: eligibility,
      }),
    ),
  )
}

test('ST-02 & ST-05: form separates partial public location from private exact address and informs when no comparable tariffs exist', () => {
  mockSuggestedRange = {
    especialidad_id: 'spec-plomeria',
    tiene_referencia: false,
    unidad_cobro: null,
    tarifa_minima: null,
    tarifa_maxima: null,
    tarifa_promedio: null,
    rango_sugerido_min: null,
    rango_sugerido_max: null,
    servicios_comparables: 0,
    mensaje: 'No hay referencia suficiente de tarifas comparables en esta categoría.',
  }
  mockCreated = null
  mockPresupuesto = ''

  const html = renderForm()
  assert.ok(html.includes('Ubicación general (visible para técnicos elegibles)'))
  assert.ok(html.includes('Dirección exacta privada (oculta hasta adjudicar)'))
  assert.ok(html.includes('No hay referencia suficiente de tarifas comparables en esta categoría.'))
})

test('ST-03 & ST-05: suggested range displays average base and unit without overwriting client amount', () => {
  mockSuggestedRange = {
    especialidad_id: 'spec-plomeria',
    tiene_referencia: true,
    unidad_cobro: 'POR_SERVICIO',
    tarifa_minima: 400,
    tarifa_maxima: 600,
    tarifa_promedio: 500,
    rango_sugerido_min: 425,
    rango_sugerido_max: 575,
    servicios_comparables: 2,
    mensaje: 'Rango sugerido calculado con 2 tarifas comparables.',
  }
  mockCreated = null
  mockPresupuesto = '320'

  const html = renderForm()
  assert.ok(html.includes('Rango sugerido:'))
  assert.ok(html.includes('por servicio'))
  assert.ok(html.includes('value="320"'))
})

test('ST-05 & CA-07: confirmation displays published state, open modality, and request identifier', () => {
  mockCreated = {
    id: 'open-req-12345',
    solicitud_id: 'open-req-12345',
    modalidad: 'ABIERTA',
    estado: 'PUBLICADA',
    cliente_id: 'client-1',
    tecnico_id: null,
    especialidad_id: 'spec-plomeria',
    especialidad: { id: 'spec-plomeria', nombre: 'Plomería' },
    descripcion: 'Reparación completa de tubería',
    municipio: 'Distrito Central',
    sector: 'Col. Palmira',
    direccion_exacta: 'Casa 24',
    fecha_hora: '2026-11-15T15:00:00Z',
    metodo_pago: 'efectivo',
    presupuesto: 850,
    monto_base: 850,
    creada_en: '2026-11-10T01:00:00Z',
  }

  const html = renderForm()
  assert.ok(html.includes('Solicitud abierta publicada correctamente'))
  assert.ok(html.includes('open-req-12345'))
  assert.ok(html.includes('PUBLICADA'))
  assert.ok(html.includes('Modalidad Abierta'))
})
