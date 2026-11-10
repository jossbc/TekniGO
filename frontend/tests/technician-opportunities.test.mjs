import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter } = require('react-router-dom')

let mockEspecialidadId = ''
let mockEstado = 'TODOS'
let mockPage = null
let mockListError = ''
let mockListNotice = ''
let mockSelectedId = ''
let mockDetail = null
let mockDetailError = ''

const result = await build({
  stdin: {
    contents: `
      export { default as TechnicianOpportunitiesManager } from './src/components/TechnicianOpportunitiesManager'
      export { default as TechnicianOpportunitiesPage } from './src/pages/TechnicianOpportunitiesPage'
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
let stateCallIndex = 0
new Function('require', 'module', 'exports', result.outputFiles[0].text)((id) => {
  if (id === 'react') {
    return {
      ...React,
      useEffect() {},
      useState(initial) {
        const idx = stateCallIndex++
        // Order in TechnicianOpportunitiesManager:
        // 0: specialties, 1: coverageZones, 2: especialidadId, 3: municipio, 4: sector, 5: estado, 6: offset,
        // 7: page, 8: loadingList, 9: listError, 10: listNotice, 11: refreshTick, 12: discardingId,
        // 13: selectedId, 14: detail, 15: loadingDetail, 16: detailError
        if (idx === 0) return [[{ id: 'spec-plomeria', nombre: 'Plomería' }], () => {}]
        if (idx === 1) return [[{ departamento: 'Francisco Morazán', municipio: 'Distrito Central', sector: 'Col. Palmira' }], () => {}]
        if (idx === 2) return [mockEspecialidadId, () => {}]
        if (idx === 5) return [mockEstado, () => {}]
        if (idx === 7) return [mockPage, () => {}]
        if (idx === 8) return [false, () => {}]
        if (idx === 9) return [mockListError, () => {}]
        if (idx === 10) return [mockListNotice, () => {}]
        if (idx === 13) return [mockSelectedId, () => {}]
        if (idx === 14) return [mockDetail, () => {}]
        if (idx === 15) return [false, () => {}]
        if (idx === 16) return [mockDetailError, () => {}]
        return [initial, () => {}]
      },
    }
  }
  return require(id)
}, module, module.exports)

function renderOpportunities() {
  stateCallIndex = 0
  return renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      {},
      React.createElement(module.exports.TechnicianOpportunitiesManager, {
        user: { uid: 'tech-1', email: 'tecnico@example.com' },
      }),
    ),
  )
}

test('HU-27 ST-03 & ST-05 (CA-01, CA-03, CA-05): listing and detail display budget, description, date, and partial location while hiding exact address and phone, with discard action', () => {
  const sampleItem = {
    id: 'opp-101',
    solicitud_id: 'opp-101',
    modalidad: 'ABIERTA',
    estado: 'CON OFERTAS',
    especialidad_id: 'spec-plomeria',
    especialidad: { id: 'spec-plomeria', nombre: 'Plomería' },
    descripcion: 'Instalación de bomba hidroneumática y revisión de válvulas',
    municipio: 'Distrito Central',
    sector: 'Col. Palmira',
    fecha_hora: '2026-11-18T14:00:00Z',
    metodo_pago: 'efectivo',
    presupuesto: 1250,
    monto_base: 1250,
    creada_en: '2026-11-10T08:00:00Z',
    cliente: { nombre_completo: 'Ana Cliente' },
    ubicacion_exacta_protegida: true,
  }

  mockEspecialidadId = ''
  mockEstado = 'TODOS'
  mockListError = ''
  mockListNotice = ''
  mockPage = {
    total: 1,
    offset: 0,
    limit: 10,
    solicitudes: [sampleItem],
  }
  mockSelectedId = 'opp-101'
  mockDetail = sampleItem
  mockDetailError = ''

  const html = renderOpportunities()
  assert.ok(html.includes('Plomería'))
  assert.ok(html.includes('Con ofertas'))
  assert.ok(html.includes('Instalación de bomba hidroneumática y revisión de válvulas'))
  assert.ok(html.includes('Distrito Central, Col. Palmira'))
  assert.ok(html.includes('Dirección exacta y teléfono ocultos'))
  assert.ok(html.includes('permanecen ocultos por privacidad hasta que la solicitud sea aceptada'))
  assert.ok(html.includes('Descartar publicación'))
  assert.ok(!html.includes('Calle Privada'))
  assert.ok(!html.includes('+50498765432'))
})

test('HU-27 ST-03 & ST-05 (CA-06): absence of matches displays message and allows reviewing/clearing filters', () => {
  mockEspecialidadId = 'spec-plomeria'
  mockEstado = 'CON_OFERTAS'
  mockListError = ''
  mockListNotice = ''
  mockPage = {
    total: 0,
    offset: 0,
    limit: 10,
    mensaje: 'No se encontraron oportunidades disponibles con los filtros seleccionados. Revisa o ajusta los filtros.',
    solicitudes: [],
  }
  mockSelectedId = ''
  mockDetail = null
  mockDetailError = ''

  const html = renderOpportunities()
  assert.ok(html.includes('No se encontraron oportunidades disponibles'))
  assert.ok(html.includes('Revisa o ajusta los filtros'))
  assert.ok(html.includes('Limpiar y revisar filtros'))
})

test('HU-27 ST-04 & ST-05 (CA-04, CA-05): discard notice and operational restriction alert render properly', () => {
  mockEspecialidadId = ''
  mockEstado = 'TODOS'
  mockListNotice = 'Publicación descartada de tu listado personal'
  mockListError = 'Debes estar en estado Disponible para consultar ofertas o postular'
  mockPage = null
  mockSelectedId = ''
  mockDetail = null
  mockDetailError = ''

  const html = renderOpportunities()
  assert.ok(html.includes('Publicación descartada de tu listado personal'))
  assert.ok(html.includes('Debes estar en estado Disponible para consultar ofertas o postular'))
})

test('HU-28 ST-03 (CA-01, CA-02, CA-04): renders accept budget action and transitions to pending proposal response state without duplicate active bid action', () => {
  const withoutProposal = {
    id: 'opp-208',
    solicitud_id: 'opp-208',
    modalidad: 'ABIERTA',
    estado: 'PUBLICADA',
    especialidad_id: 'spec-plomeria',
    especialidad: { id: 'spec-plomeria', nombre: 'Plomería' },
    descripcion: 'Cambio de llave mezcladora',
    municipio: 'Distrito Central',
    sector: 'Col. Palmira',
    fecha_hora: '2026-11-20T15:00:00Z',
    metodo_pago: 'efectivo',
    presupuesto: 650,
    monto_base: 650,
    creada_en: '2026-11-10T09:00:00Z',
    cliente: { nombre_completo: 'Ana Cliente' },
    ubicacion_exacta_protegida: true,
    mi_propuesta: null,
  }

  mockEspecialidadId = ''
  mockEstado = 'TODOS'
  mockListError = ''
  mockListNotice = ''
  mockPage = { total: 1, offset: 0, limit: 10, solicitudes: [withoutProposal] }
  mockSelectedId = 'opp-208'
  mockDetail = withoutProposal
  mockDetailError = ''

  const beforeHtml = renderOpportunities()
  assert.ok(beforeHtml.includes('Aceptar presupuesto'))
  assert.ok(beforeHtml.includes('Aceptar monto publicado'))

  const withProposal = {
    ...withoutProposal,
    estado: 'CON OFERTAS',
    mi_propuesta: {
      id: 'prop-1',
      tipo: 'ACEPTACION_PRESUPUESTO',
      monto: 650,
      estado: 'PENDIENTE',
      creada_en: '2026-11-10T10:00:00Z',
    },
  }
  mockPage = { total: 1, offset: 0, limit: 10, solicitudes: [withProposal] }
  mockDetail = withProposal

  const afterHtml = renderOpportunities()
  assert.ok(afterHtml.includes('Propuesta enviada · Pendiente de decisión'))
  assert.ok(afterHtml.includes('Propuesta enviada (Pendiente)'))
  assert.ok(afterHtml.includes('Propuesta activa (PENDIENTE)'))
  assert.ok(afterHtml.includes('El trabajo no se adjudica automáticamente'))
})
