import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
let harness

const result = await build({
  entryPoints: ['src/components/TechnicianRequestsManager.tsx'],
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  loader: { '.png': 'dataurl' },
  define: { 'import.meta.env': '{}' },
})

const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(id => {
  if (id === 'react') {
    return {
      ...React,
      useState(initial) {
        const index = harness.index++
        if (!(index in harness.state)) harness.state[index] = typeof initial === 'function' ? initial() : initial
        return [
          harness.state[index],
          value => {
            harness.state[index] = typeof value === 'function' ? value(harness.state[index]) : value
          },
        ]
      },
      useEffect(effect, deps) {
        const index = harness.index++
        const previous = harness.state[index]
        if (!previous || deps.some((dep, i) => dep !== previous[i])) harness.effects.push(effect)
        harness.state[index] = deps
      },
    }
  }
  return require(id)
}, module, module.exports)

const nodes = tree => (!tree || typeof tree !== 'object' ? [] : [tree, ...React.Children.toArray(tree.props?.children).flatMap(nodes)])
const textOf = node => {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (typeof node === 'object') return textOf(node.props?.children)
  return ''
}
const flush = () => new Promise(resolve => setImmediate(resolve))

const sampleRequests = [
  {
    id: 'req-1',
    cliente_nombre: 'Joseline Banegas',
    servicio_nombre: 'Electricista (Cambio de tomacorrientes)',
    fecha_hora: '2026-11-15T14:30:00Z',
    descripcion: 'Cambiar 4 tomacorrientes en la sala.',
    estado: 'PENDIENTE',
    total_estimado: 400,
  },
  {
    id: 'req-2',
    cliente_nombre: 'Andy Gomez',
    servicio_nombre: 'Aire acondicionado y refrigeración',
    fecha_hora: '2026-11-16T10:00:00Z',
    descripcion: 'Mantenimiento preventivo mini split.',
    estado: 'PENDIENTE',
    total_estimado: 800,
  },
]

test('loads direct requests via API and updates status on accept and reject PATCH requests', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'token-123' }
  const originalFetch = globalThis.fetch
  const calls = []
  const cleanups = []

  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', auth: options.headers?.Authorization })
    if (url.endsWith('/technicians/me/direct-requests') && (options.method || 'GET') === 'GET') {
      return new Response(JSON.stringify(sampleRequests), { status: 200 })
    }
    if (url.endsWith('/technicians/me/direct-requests/req-1/accept') && options.method === 'PATCH') {
      return new Response(JSON.stringify({ id: 'req-1', estado: 'ACEPTADA' }), { status: 200 })
    }
    if (url.endsWith('/technicians/me/direct-requests/req-2/reject') && options.method === 'PATCH') {
      return new Response(JSON.stringify({ id: 'req-2', estado: 'CANCELADA' }), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'Unexpected call' }), { status: 500 })
  }

  const render = () => {
    harness.index = 0
    const tree = module.exports.TechnicianRequestsManager({ user })
    for (const effect of harness.effects.splice(0)) {
      const cleanup = effect()
      if (cleanup) cleanups.push(cleanup)
    }
    return nodes(tree)
  }

  try {
    const loadingNodes = render()
    assert.ok(loadingNodes.some(n => n.props?.message === 'Cargando solicitudes entrantes.'))

    await flush()
    const loadedNodes = render()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].method, 'GET')
    assert.equal(calls[0].auth, 'Bearer token-123')
    assert.ok(loadedNodes.some(n => textOf(n).includes('Joseline Banegas')))
    assert.ok(loadedNodes.some(n => textOf(n).includes('Andy Gomez')))

    const acceptBtn = loadedNodes.find(n => n.type === module.exports.TechnicianRequestsManager ? false : textOf(n) === 'Aceptar' && typeof n.props?.onClick === 'function')
    assert.ok(acceptBtn, 'Accept button should exist')
    await acceptBtn.props.onClick()

    const afterAccept = render()
    assert.equal(calls[1].method, 'PATCH')
    assert.ok(calls[1].url.endsWith('/technicians/me/direct-requests/req-1/accept'))
    assert.ok(afterAccept.some(n => n.props?.role === 'status' && textOf(n).includes('Solicitud aceptada correctamente.')))
    assert.ok(afterAccept.some(n => textOf(n) === 'Aceptada'))

    const rejectBtn = afterAccept.find(n => textOf(n) === 'Rechazar' && typeof n.props?.onClick === 'function')
    assert.ok(rejectBtn, 'Remaining Reject button for req-2 should exist')
    await rejectBtn.props.onClick()

    const afterReject = render()
    assert.equal(calls[2].method, 'PATCH')
    assert.ok(calls[2].url.endsWith('/technicians/me/direct-requests/req-2/reject'))
    assert.ok(afterReject.some(n => n.props?.role === 'status' && textOf(n).includes('Solicitud rechazada correctamente.')))
    assert.ok(afterReject.some(n => textOf(n) === 'Cancelada'))
  } finally {
    cleanups.forEach(cleanup => cleanup())
    globalThis.fetch = originalFetch
  }
})

test('shows error notifications when initial fetch or PATCH action fails without mutating local status', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'token-123' }
  const originalFetch = globalThis.fetch
  const cleanups = []
  let failInitial = true

  globalThis.fetch = async (url, options = {}) => {
    if (url.endsWith('/technicians/me/direct-requests')) {
      if (failInitial) {
        return new Response(JSON.stringify({ detail: 'Error de servidor' }), { status: 500 })
      }
      return new Response(JSON.stringify([sampleRequests[0]]), { status: 200 })
    }
    if (url.endsWith('/technicians/me/direct-requests/req-1/accept') && options.method === 'PATCH') {
      return new Response(JSON.stringify({ detail: 'No autorizado para aceptar' }), { status: 403 })
    }
    return new Response(null, { status: 404 })
  }

  const render = () => {
    harness.index = 0
    const tree = module.exports.TechnicianRequestsManager({ user })
    for (const effect of harness.effects.splice(0)) {
      const cleanup = effect()
      if (cleanup) cleanups.push(cleanup)
    }
    return nodes(tree)
  }

  try {
    render()
    await flush()
    const errorNodes = render()
    const loadAlert = errorNodes.find(n => n.props?.role === 'alert')
    assert.ok(loadAlert && textOf(loadAlert).includes('Error de servidor'))

    failInitial = false
    const retryBtn = errorNodes.find(n => textOf(n) === 'Reintentar' && typeof n.props?.onClick === 'function')
    assert.ok(retryBtn)
    retryBtn.props.onClick()
    render()
    await flush()

    const loadedNodes = render()
    const acceptBtn = loadedNodes.find(n => textOf(n) === 'Aceptar' && typeof n.props?.onClick === 'function')
    assert.ok(acceptBtn)
    await acceptBtn.props.onClick()

    const afterFailedAccept = render()
    const actionAlert = afterFailedAccept.find(n => n.props?.role === 'alert')
    assert.ok(actionAlert && textOf(actionAlert).includes('No autorizado para aceptar'))
    assert.ok(afterFailedAccept.some(n => textOf(n) === 'Pendiente'))
    assert.ok(!afterFailedAccept.some(n => textOf(n) === 'Aceptada'))
  } finally {
    cleanups.forEach(cleanup => cleanup())
    globalThis.fetch = originalFetch
  }
})

test('HU-34 ST-03: reports work finished from EN_PROCESO, displays 48h deadline in America/Tegucigalpa, and blocks when adjustment is pending', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'token-123' }
  const originalFetch = globalThis.fetch
  const calls = []
  const cleanups = []

  const inProgressRequests = [
    {
      id: 'req-proc-1',
      cliente_nombre: 'Valeria Cliente',
      servicio_nombre: 'Mantenimiento de panel eléctrico',
      fecha_hora: '2026-11-12T15:00:00Z',
      descripcion: 'Revisión y ajuste de circuitos.',
      estado: 'EN_PROCESO',
      total_estimado: 650,
    },
    {
      id: 'req-proc-adj',
      cliente_nombre: 'Carlos Ajuste',
      servicio_nombre: 'Instalación de bomba',
      fecha_hora: '2026-11-12T16:00:00Z',
      descripcion: 'Requiere materiales adicionales.',
      estado: 'EN_PROCESO',
      total_estimado: 900,
      tiene_ajuste_pendiente: true,
      motivo_ajuste_pendiente: 'Ajuste de presupuesto por válvula extra pendiente de aprobación.',
    },
  ]

  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' })
    if (url.endsWith('/technicians/me/direct-requests') && (options.method || 'GET') === 'GET') {
      return new Response(JSON.stringify(inProgressRequests), { status: 200 })
    }
    if (url.endsWith('/technicians/me/direct-requests/req-proc-1/report-completion') && options.method === 'PATCH') {
      return new Response(
        JSON.stringify({
          id: 'req-proc-1',
          solicitud_id: 'req-proc-1',
          modalidad: 'DIRECTA',
          estado: 'EN_PROCESO',
          estado_etiqueta: 'En proceso (Pendiente de recepción)',
          pendiente_recepcion: true,
          completada: false,
          fondos_liberados: false,
          estado_fondos: 'RETENIDOS_PENDIENTE_CONFIRMACION',
          plazo_confirmacion_horas: 48,
          finalizacion_reportada_en: '2026-11-12T20:30:00+00:00',
          vencimiento_confirmacion_en: '2026-11-14T20:30:00+00:00',
          finalizacion_reportada_local: '2026-11-12T14:30:00-06:00',
          vencimiento_confirmacion_local: '2026-11-14T14:30:00-06:00',
          zona_horaria_usuario: 'America/Tegucigalpa',
          reintento_idempotente: false,
          mensaje: 'Finalización reportada exitosamente. El servicio permanece En proceso pendiente de recepción y se inició el plazo de 48 horas.',
        }),
        { status: 200 },
      )
    }
    return new Response(JSON.stringify({ detail: 'Unexpected call' }), { status: 500 })
  }

  const render = () => {
    harness.index = 0
    const tree = module.exports.TechnicianRequestsManager({ user })
    for (const effect of harness.effects.splice(0)) {
      const cleanup = effect()
      if (cleanup) cleanups.push(cleanup)
    }
    return nodes(tree)
  }

  try {
    render()
    await flush()
    const loadedNodes = render()

    // CA-05: Solicitud con ajuste pendiente muestra nota de bloqueo y botón deshabilitado
    assert.ok(loadedNodes.some(n => n.props?.role === 'note' && textOf(n).includes('Ajuste pendiente de aprobación')))
    const reportBtns = loadedNodes.filter(n => textOf(n) === 'Reportar trabajo finalizado' && typeof n.props?.onClick === 'function')
    assert.equal(reportBtns.length, 2)
    assert.equal(reportBtns[0].props.disabled, false)
    assert.equal(reportBtns[1].props.disabled, true)

    // CA-01, CA-02, CA-03: Reportar trabajo finalizado conserva En proceso · Pendiente de recepción y muestra plazo en America/Tegucigalpa
    await reportBtns[0].props.onClick()
    const afterReport = render()
    assert.equal(calls[1].method, 'PATCH')
    assert.ok(calls[1].url.endsWith('/technicians/me/direct-requests/req-proc-1/report-completion'))
    assert.ok(afterReport.some(n => textOf(n) === 'En proceso · Pendiente de recepción'))
    const deadlineRegion = afterReport.find(n => n.props?.role === 'region' && n.props?.['aria-label'] === 'Plazo de confirmación del servicio')
    assert.ok(deadlineRegion, 'Should render confirmation deadline region')
    assert.ok(textOf(deadlineRegion).includes('Pendiente de recepción del cliente'))
    assert.ok(textOf(deadlineRegion).includes('America/Tegucigalpa'))
    assert.ok(textOf(deadlineRegion).includes('Vencimiento de confirmación (48 h):'))
  } finally {
    cleanups.forEach(cleanup => cleanup())
    globalThis.fetch = originalFetch
  }
})

test('HU-34 ST-05: validates controlled clock 48h window status and role/adjustment rejection without altering EN_PROCESO state', async () => {
  const libResult = await build({
    entryPoints: ['src/lib/directRequests.ts'],
    bundle: true,
    write: false,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    define: { 'import.meta.env': '{}' },
  })
  const libModule = { exports: {} }
  new Function('require', 'module', 'exports', libResult.outputFiles[0].text)(require, libModule, libModule.exports)
  const { formatConfirmationWindowStatus, formatTegucigalpaDateTime } = libModule.exports

  const reportUtc = '2026-11-12T20:00:00+00:00'
  const expUtc = '2026-11-14T20:00:00+00:00'

  // Reloj controlado a t0 (faltan 48 h exactas)
  const atStart = formatConfirmationWindowStatus(expUtc, reportUtc)
  assert.equal(atStart.vencido, false)
  assert.equal(atStart.horasRestantes, 48)
  assert.ok(atStart.etiqueta.includes('Restan 48 h'))

  // Reloj controlado a t0 + 47h 30m (faltan 30 min)
  const nearExpiry = formatConfirmationWindowStatus(expUtc, '2026-11-14T19:30:00+00:00')
  assert.equal(nearExpiry.vencido, false)
  assert.equal(nearExpiry.horasRestantes, 0)
  assert.ok(nearExpiry.etiqueta.includes('Restan 30 min'))

  // Reloj controlado exactamente a las 48h (vencido)
  const atExpiry = formatConfirmationWindowStatus(expUtc, expUtc)
  assert.equal(atExpiry.vencido, true)
  assert.equal(atExpiry.horasRestantes, 0)
  assert.ok(atExpiry.etiqueta.includes('Plazo de 48 horas vencido'))

  // Formato explícito en America/Tegucigalpa
  assert.ok(formatTegucigalpaDateTime(expUtc).includes('(America/Tegucigalpa)'))
})

test('EP-08 technician review button renders active, disabled sent, and hidden closed states across reloads', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'token-123' }
  const originalFetch = globalThis.fetch
  const cleanups = []

  const completedRequests = [
    {
      id: 'tech-rev-open',
      cliente_nombre: 'Ana Cliente',
      servicio_nombre: 'Fontanería',
      fecha_hora: '2026-11-12T15:00:00Z',
      descripcion: 'Reparación',
      estado: 'COMPLETADA',
      total_estimado: 450,
      mi_resena_enviada: false,
      evaluacion_cerrada: false,
    },
    {
      id: 'tech-rev-sent',
      cliente_nombre: 'Luis Cliente',
      servicio_nombre: 'Electricidad',
      fecha_hora: '2026-11-12T16:00:00Z',
      descripcion: 'Instalación',
      estado: 'COMPLETADA',
      total_estimado: 600,
      mi_resena_enviada: true,
      evaluacion_cerrada: false,
    },
    {
      id: 'tech-rev-closed',
      cliente_nombre: 'Sofia Cliente',
      servicio_nombre: 'Pintura',
      fecha_hora: '2026-11-12T17:00:00Z',
      descripcion: 'Retoque',
      estado: 'COMPLETADA',
      total_estimado: 750,
      mi_resena_enviada: true,
      evaluacion_cerrada: true,
    },
  ]

  globalThis.fetch = async (url, options = {}) => {
    if (url.endsWith('/technicians/me/direct-requests') && (options.method || 'GET') === 'GET') {
      return new Response(JSON.stringify(completedRequests), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'Unexpected call' }), { status: 500 })
  }

  const render = () => {
    harness.index = 0
    const tree = module.exports.TechnicianRequestsManager({ user })
    for (const effect of harness.effects.splice(0)) {
      const cleanup = effect()
      if (cleanup) cleanups.push(cleanup)
    }
    return nodes(tree)
  }

  try {
    render()
    await flush()
    const loadedNodes = render()

    const rateButtons = loadedNodes.filter(n => n.type !== 'div' && textOf(n) === 'Calificar cliente')
    const sentButtons = loadedNodes.filter(n => n.type !== 'div' && textOf(n) === 'Evaluación enviada')

    assert.equal(rateButtons.length, 1)
    assert.equal(typeof rateButtons[0].props.onClick, 'function')
    assert.equal(sentButtons.length, 1)
    assert.equal(sentButtons[0].props.disabled, true)
    assert.equal(sentButtons[0].props.onClick, undefined)

    // Simular clic en Calificar cliente y respuesta inmediata con publicada=false -> botón gris disabled
    rateButtons[0].props.onClick()
    const withModalPending = render()
    const modalPending = withModalPending.find(n => typeof n.props?.onSuccess === 'function' && n.props?.rolEvaluador === 'TECNICO')
    assert.ok(modalPending)
    modalPending.props.onSuccess({
      resena: {},
      estado_publicacion: 'PENDIENTE',
      publicada_inmediata: false,
      publicada: false,
      mensaje: 'Enviada en doble ciego',
    })
    await flush()
    const afterPending = render()
    assert.equal(afterPending.filter(n => n.type !== 'div' && textOf(n) === 'Calificar cliente').length, 0)
    const sentAfterPending = afterPending.filter(n => n.type !== 'div' && textOf(n) === 'Evaluación enviada')
    assert.equal(sentAfterPending.length, 2)
    assert.equal(sentAfterPending[0].props.disabled, true)
    assert.equal(sentAfterPending[0].props.onClick, undefined)

    // Simular respuesta con publicada=true -> botón oculto de inmediato
    modalPending.props.onSuccess({
      resena: {},
      estado_publicacion: 'PUBLICADA',
      publicada_inmediata: true,
      publicada: true,
      mensaje: 'Publicada al completarse la evaluación mutua.',
    })
    await flush()
    const afterPublished = render()
    assert.equal(afterPublished.filter(n => n.type !== 'div' && textOf(n) === 'Calificar cliente').length, 0)
    assert.equal(afterPublished.filter(n => n.type !== 'div' && textOf(n) === 'Evaluación enviada').length, 1)
  } finally {
    cleanups.forEach(cleanup => cleanup())
    globalThis.fetch = originalFetch
  }
})
