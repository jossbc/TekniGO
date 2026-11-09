import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter } = require('react-router-dom')

let mockPage = null
let mockDetail = null
let mockDetailError = ''
let mockSelectedId = ''
let mockRoles = ['CLIENTE']

const result = await build({
  stdin: {
    contents: `
      export { default as ClientRequestsManager } from './src/components/ClientRequestsManager'
      export { default as ClientRequestsPage } from './src/pages/ClientRequestsPage'
      export * from './src/lib/clientRequests'
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
  plugins: [
    {
      name: 'account-state',
      setup(buildPlugin) {
        buildPlugin.onResolve({ filter: /\/useAccount$/ }, () => ({ path: 'test:account', external: true }))
      },
    },
  ],
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
        // Order in ClientRequestsManager:
        // 0: modalidad, 1: estado, 2: offset, 3: page, 4: loadingList, 5: listError, 6: refreshTick,
        // 7: selectedId, 8: detail, 9: loadingDetail, 10: detailError, 11: executingAction, 12: actionNotice
        if (idx === 3) return [mockPage, () => {}]
        if (idx === 4) return [false, () => {}]
        if (idx === 7) return [mockSelectedId, () => {}]
        if (idx === 8) return [mockDetail, () => {}]
        if (idx === 9) return [false, () => {}]
        if (idx === 10) return [mockDetailError, () => {}]
        return [initial, () => {}]
      },
    }
  }
  if (id === 'test:account') {
    return {
      useAccount: () => ({
        state: {
          status: 'ready',
          profile: {
            id: 'client-1',
            roles: mockRoles,
            nombre_completo: 'Ana Cliente',
            estado_identidad: 'VERIFICADO',
          },
        },
        retry() {},
        updateProfile() {},
      }),
    }
  }
  return require(id)
}, module, module.exports)

function renderManager() {
  stateCallIndex = 0
  return renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      {},
      React.createElement(module.exports.ClientRequestsManager, { user: { uid: 'client-1', email: 'ana@example.com' } }),
    ),
  )
}

test('ST-05: empty results render informative empty state without error alert', () => {
  mockPage = {
    total: 0,
    offset: 0,
    limit: 10,
    modalidad: 'TODOS',
    estado: 'TODOS',
    solicitudes: [],
  }
  mockSelectedId = ''
  mockDetail = null
  mockDetailError = ''

  const html = renderManager()
  assert.ok(html.includes('No hay solicitudes para mostrar'))
  assert.ok(html.includes('Buscar servicios'))
  assert.ok(!html.includes('role="alert"'))
})

test('ST-05: final states (COMPLETADA, CANCELADA, RECHAZADA) render timeline, agreed proposal, and hide modification actions', () => {
  for (const finalState of ['COMPLETADA', 'CANCELADA', 'RECHAZADA']) {
    mockPage = {
      total: 1,
      offset: 0,
      limit: 10,
      modalidad: 'TODOS',
      estado: 'TODOS',
      solicitudes: [
        {
          id: 'req-1',
          modalidad: 'DIRECTA',
          estado: finalState,
          fecha_hora: '2026-11-12T15:00:00Z',
          creada_en: '2026-11-09T10:00:00Z',
          presupuesto: 700,
          monto_base: 700,
          cantidad: 2,
          tarifa_aplicada: 350,
          unidad_cobro: 'POR_SERVICIO',
          metodo_pago: 'efectivo',
          municipio: 'Distrito Central',
          sector: 'Col. Palmira',
          direccion_exacta: 'Casa 12',
          descripcion: 'Instalación eléctrica',
          servicio: { id: 'srv-1', nombre: 'Instalación de tomacorrientes', especialidad_id: 'esp-1' },
          tecnico: { usuario_id: 'tech-uuid-1', firebase_uid: 'tech-1', nombre_completo: 'Carlos Técnico' },
        },
      ],
    }
    mockSelectedId = 'req-1'
    mockDetailError = ''
    mockDetail = {
      ...mockPage.solicitudes[0],
      tecnico: {
        usuario_id: 'tech-uuid-1',
        firebase_uid: 'tech-1',
        nombre_completo: 'Carlos Técnico',
        estado_verificacion: 'VERIFICADO',
        calificacion_promedio: 4.8,
      },
      propuesta: {
        servicio_id: 'srv-1',
        servicio_nombre: 'Instalación de tomacorrientes',
        cantidad: 2,
        tarifa_aplicada: 350,
        unidad_cobro: 'POR_SERVICIO',
        monto_pactado: 700,
        metodo_pago: 'efectivo',
      },
      linea_tiempo: [
        { estado: 'PENDIENTE', descripcion: 'Solicitud registrada.', fecha: '2026-11-09T10:00:00Z' },
        { estado: finalState, descripcion: 'Estado final registrado.', fecha: '2026-11-10T12:00:00Z' },
      ],
      acciones_permitidas: [],
    }

    const html = renderManager()
    assert.ok(html.includes('Carlos Técnico'))
    assert.ok(html.includes('Propuesta pactada'))
    assert.ok(html.includes('Línea de tiempo de cambios'))
    assert.ok(html.includes('no admite modificaciones ni acciones adicionales'))
    assert.ok(!html.includes('>Cancelar solicitud<'))
    assert.ok(!html.includes('>Confirmar finalización<'))
  }
})

test('ST-05: pending request shows only allowed cancel action and foreign request error hides private data', () => {
  mockPage = {
    total: 1,
    offset: 0,
    limit: 10,
    modalidad: 'TODOS',
    estado: 'PENDIENTE',
    solicitudes: [
      {
        id: 'req-pending',
        modalidad: 'DIRECTA',
        estado: 'PENDIENTE',
        fecha_hora: '2026-11-12T15:00:00Z',
        creada_en: '2026-11-09T10:00:00Z',
        presupuesto: 350,
        monto_base: 350,
        cantidad: 1,
        tarifa_aplicada: 350,
        unidad_cobro: 'POR_SERVICIO',
        metodo_pago: 'efectivo',
        municipio: 'Distrito Central',
        sector: 'Kennedy',
        direccion_exacta: 'Bloque 4',
        descripcion: 'Revisión',
        servicio: { id: 'srv-1', nombre: 'Revisión eléctrica', especialidad_id: 'esp-1' },
        tecnico: { usuario_id: 'tech-uuid-1', firebase_uid: 'tech-1', nombre_completo: 'Carlos Técnico' },
      },
    ],
  }
  mockSelectedId = 'req-pending'
  mockDetailError = ''
  mockDetail = {
    ...mockPage.solicitudes[0],
    propuesta: {
      servicio_id: 'srv-1',
      servicio_nombre: 'Revisión eléctrica',
      cantidad: 1,
      tarifa_aplicada: 350,
      unidad_cobro: 'POR_SERVICIO',
      monto_pactado: 350,
      metodo_pago: 'efectivo',
    },
    linea_tiempo: [{ estado: 'PENDIENTE', descripcion: 'Solicitud registrada.', fecha: '2026-11-09T10:00:00Z' }],
    acciones_permitidas: ['CANCELAR'],
  }

  const pendingHtml = renderManager()
  assert.ok(pendingHtml.includes('Cancelar solicitud'))
  assert.ok(!pendingHtml.includes('Confirmar finalización'))

  // Solicitud ajena consultada por URL/ID produce error de acceso sin mostrar datos ajenos
  mockSelectedId = 'foreign-req-99'
  mockDetail = null
  mockDetailError = 'No tienes permiso para consultar esta solicitud'
  const foreignHtml = renderManager()
  assert.ok(foreignHtml.includes('No tienes permiso para consultar esta solicitud'))
  assert.ok(!foreignHtml.includes('Propuesta pactada'))
})
