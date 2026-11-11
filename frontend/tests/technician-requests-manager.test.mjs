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
      return new Response(JSON.stringify({ id: 'req-2', estado: 'RECHAZADA' }), { status: 200 })
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
    assert.ok(afterReject.some(n => textOf(n) === 'Rechazada'))
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
