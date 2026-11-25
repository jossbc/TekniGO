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
      export { default as ClientRequestsManager, resolveReviewButtonState } from './src/components/ClientRequestsManager'
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

test('HU-28 ST-04 & ST-05 (CA-05): open request in CON OFERTAS state renders received technician proposals', () => {
  mockPage = {
    total: 1,
    offset: 0,
    limit: 10,
    modalidad: 'ABIERTA',
    estado: 'CON OFERTAS',
    solicitudes: [
      {
        id: 'open-req-offers',
        modalidad: 'ABIERTA',
        estado: 'CON OFERTAS',
        fecha_hora: '2026-11-18T15:00:00Z',
        creada_en: '2026-11-10T10:00:00Z',
        presupuesto: 1050,
        monto_base: 1050,
        cantidad: null,
        tarifa_aplicada: null,
        unidad_cobro: null,
        metodo_pago: 'efectivo',
        municipio: 'Distrito Central',
        sector: 'Col. Palmira',
        direccion_exacta: 'Casa 14',
        descripcion: 'Reparación general de tubería',
        servicio: null,
        tecnico: null,
      },
    ],
  }
  mockSelectedId = 'open-req-offers'
  mockDetailError = ''
  mockDetail = {
    ...mockPage.solicitudes[0],
    propuesta: {
      servicio_id: null,
      servicio_nombre: 'Plomería',
      cantidad: null,
      tarifa_aplicada: null,
      unidad_cobro: null,
      monto_pactado: 1050,
      metodo_pago: 'efectivo',
    },
    propuestas_recibidas: [
      {
        id: 'prop-a',
        propuesta_id: 'prop-a',
        solicitud_id: 'open-req-offers',
        usuario_id: 'tech-uuid-a',
        tecnico_id: 'tech-uid-a',
        tipo: 'ACEPTACION_PRESUPUESTO',
        monto: 1050,
        justificacion: null,
        estado: 'PENDIENTE',
        activa: true,
        creada_en: '2026-11-10T10:15:00Z',
        tecnico: {
          usuario_id: 'tech-uuid-a',
          firebase_uid: 'tech-uid-a',
          nombre_completo: 'Técnico Concurrente 1',
          estado_verificacion: 'VERIFICADO',
          calificacion_promedio: 4.9,
        },
      },
      {
        id: 'prop-b',
        propuesta_id: 'prop-b',
        solicitud_id: 'open-req-offers',
        usuario_id: 'tech-uuid-b',
        tecnico_id: 'tech-uid-b',
        tipo: 'ACEPTACION_PRESUPUESTO',
        monto: 1050,
        justificacion: null,
        estado: 'PENDIENTE',
        activa: true,
        creada_en: '2026-11-10T10:18:00Z',
        tecnico: {
          usuario_id: 'tech-uuid-b',
          firebase_uid: 'tech-uid-b',
          nombre_completo: 'Técnico Concurrente 2',
          estado_verificacion: 'VERIFICADO',
          calificacion_promedio: 4.7,
        },
      },
    ],
    linea_tiempo: [
      { estado: 'PUBLICADA', descripcion: 'Solicitud publicada.', fecha: '2026-11-10T10:00:00Z' },
      { estado: 'CON OFERTAS', descripcion: 'Propuestas recibidas.', fecha: '2026-11-10T10:15:00Z' },
    ],
    acciones_permitidas: ['CANCELAR'],
  }

  const html = renderManager()
  assert.ok(html.includes('Propuestas recibidas (2)'))
  assert.ok(html.includes('Técnico Concurrente 1'))
  assert.ok(html.includes('Técnico Concurrente 2'))
  assert.ok(html.includes('Con ofertas'))
})

test('HU-34 ST-03 (CA-02 & CA-03): EN_PROCESO request with reported completion shows pending reception badge and 48h deadline in America/Tegucigalpa', () => {
  mockPage = {
    total: 1,
    offset: 0,
    limit: 10,
    modalidad: 'DIRECTA',
    estado: 'EN_PROCESO',
    solicitudes: [
      {
        id: 'req-hu34-1',
        modalidad: 'DIRECTA',
        estado: 'EN_PROCESO',
        fecha_hora: '2026-11-12T15:00:00Z',
        creada_en: '2026-11-10T10:00:00Z',
        finalizacion_reportada_en: '2026-11-12T20:30:00+00:00',
        vencimiento_confirmacion_en: '2026-11-14T20:30:00+00:00',
        finalizacion_reportada_local: '2026-11-12T14:30:00-06:00',
        vencimiento_confirmacion_local: '2026-11-14T14:30:00-06:00',
        pendiente_recepcion: true,
        presupuesto: 650,
        monto_base: 650,
        cantidad: 1,
        tarifa_aplicada: 650,
        unidad_cobro: 'POR_SERVICIO',
        metodo_pago: 'tarjeta',
        municipio: 'Distrito Central',
        sector: 'Col. Palmira',
        direccion_exacta: 'Casa 18',
        descripcion: 'Revisión de panel principal',
        servicio: { id: 'srv-1', nombre: 'Mantenimiento de panel eléctrico', especialidad_id: 'esp-1' },
        tecnico: { usuario_id: 'tech-uuid-1', firebase_uid: 'tech-1', nombre_completo: 'Ricardo Técnico' },
      },
    ],
  }
  mockSelectedId = 'req-hu34-1'
  mockDetailError = ''
  mockDetail = {
    ...mockPage.solicitudes[0],
    propuesta: {
      servicio_id: 'srv-1',
      servicio_nombre: 'Mantenimiento de panel eléctrico',
      cantidad: 1,
      tarifa_aplicada: 650,
      unidad_cobro: 'POR_SERVICIO',
      monto_pactado: 650,
      metodo_pago: 'tarjeta',
    },
    linea_tiempo: [
      { estado: 'PENDIENTE', descripcion: 'Solicitud registrada.', fecha: '2026-11-10T10:00:00Z' },
      { estado: 'EN_PROCESO', descripcion: 'Servicio en ejecución.', fecha: '2026-11-12T15:00:00Z' },
    ],
    acciones_permitidas: ['COMPLETAR'],
  }

  const html = renderManager()
  assert.ok(html.includes('En proceso · Pendiente de recepción'))
  assert.ok(html.includes('Trabajo reportado como finalizado · Pendiente de recepción'))
  assert.ok(html.includes('Vencimiento de confirmación (48 h):'))
  assert.ok(html.includes('America/Tegucigalpa'))
})

test('EP-08 review button lifecycle renders active, disabled sent, and hidden closed states cleanly', () => {
  const baseCompleted = {
    modalidad: 'DIRECTA',
    estado: 'COMPLETADA',
    fecha_hora: '2026-11-12T15:00:00Z',
    creada_en: '2026-11-10T10:00:00Z',
    presupuesto: 500,
    monto_base: 500,
    cantidad: 1,
    tarifa_aplicada: 500,
    unidad_cobro: 'POR_SERVICIO',
    metodo_pago: 'efectivo',
    municipio: 'Distrito Central',
    sector: 'Palmira',
    direccion_exacta: 'Casa 1',
    descripcion: 'Servicio finalizado',
    servicio: { id: 'srv-1', nombre: 'Electricidad', especialidad_id: 'esp-1' },
    tecnico: { usuario_id: 'tech-uuid-1', firebase_uid: 'tech-1', nombre_completo: 'Carlos Técnico' },
  }

  mockSelectedId = ''
  mockDetail = null
  mockDetailError = ''

  // 1. Pendiente de calificar: botón activo Calificar técnico
  mockPage = {
    total: 1,
    offset: 0,
    limit: 10,
    modalidad: 'TODOS',
    estado: 'COMPLETADA',
    solicitudes: [{ ...baseCompleted, id: 'req-rev-1', mi_resena_enviada: false, evaluacion_cerrada: false }],
  }
  const activeHtml = renderManager()
  assert.ok(activeHtml.includes('>Calificar técnico<'))

  // 2. Reseña enviada aún dentro de ventana de 48h: botón gris deshabilitado "Evaluación enviada"
  mockPage = {
    ...mockPage,
    solicitudes: [{ ...baseCompleted, id: 'req-rev-2', mi_resena_enviada: true, evaluacion_cerrada: false }],
  }
  const sentHtml = renderManager()
  assert.ok(sentHtml.includes('disabled=""') || sentHtml.includes('disabled'))
  assert.ok(sentHtml.includes('>Evaluación enviada<'))
  assert.ok(!sentHtml.includes('>Calificar técnico<'))

  // 3. Evaluación cerrada (bilateral o publicada): botón completamente oculto
  mockPage = {
    ...mockPage,
    solicitudes: [{ ...baseCompleted, id: 'req-rev-3', mi_resena_enviada: true, evaluacion_cerrada: true }],
  }
  const closedHtml = renderManager()
  assert.ok(closedHtml.includes('>Ver detalle<'))
  assert.ok(!closedHtml.includes('>Calificar técnico<'))
  assert.ok(!closedHtml.includes('>Evaluación enviada<'))

  // 4. Transición inmediata según respuesta `publicada` (Case-insensitive UUID + true/false)
  const { resolveReviewButtonState } = module.exports
  const upperUuid = '7A9EEB5B-06FF-4C9F-A0DA-45865114ECC6'
  const lowerUuid = upperUuid.toLowerCase()
  assert.equal(resolveReviewButtonState({ id: upperUuid }, {}), 'ACTIVE')
  assert.equal(
    resolveReviewButtonState({ id: upperUuid }, { [lowerUuid]: { sent: true, closed: false } }),
    'SENT_DISABLED',
  )
  assert.equal(
    resolveReviewButtonState({ id: upperUuid }, { [lowerUuid]: { sent: true, closed: true } }),
    'HIDDEN',
  )
})
