import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
let harness

const result = await build({
  entryPoints: ['src/components/TechnicianIncomingRequestsCard.tsx'],
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

test('dashboard card fetches direct requests, filters only PENDIENTE items, and handles PATCH accept', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'token-abc' }
  const originalFetch = globalThis.fetch
  const cleanups = []
  const calls = []

  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', auth: options.headers?.Authorization })
    if (url.endsWith('/technicians/me/direct-requests')) {
      return new Response(
        JSON.stringify([
          {
            id: 'req-pendiente',
            cliente_nombre: 'Joseline Banegas',
            servicio_nombre: 'Electricista',
            fecha_hora: '2026-11-15T14:30:00Z',
            descripcion: 'Tomacorrientes',
            estado: 'PENDIENTE',
            total_estimado: 400,
          },
          {
            id: 'req-aceptada',
            cliente_nombre: 'Carlos Ya Aceptado',
            servicio_nombre: 'Plomería',
            fecha_hora: '2026-11-16T10:00:00Z',
            descripcion: 'Grifo',
            estado: 'ACEPTADA',
            total_estimado: 600,
          },
        ]),
        { status: 200 },
      )
    }
    if (url.endsWith('/technicians/me/direct-requests/req-pendiente/accept') && options.method === 'PATCH') {
      return new Response(JSON.stringify({ id: 'req-pendiente', estado: 'ACEPTADA' }), { status: 200 })
    }
    return new Response(null, { status: 404 })
  }

  const render = () => {
    harness.index = 0
    const tree = module.exports.default({ user })
    for (const effect of harness.effects.splice(0)) {
      const cleanup = effect()
      if (cleanup) cleanups.push(cleanup)
    }
    return nodes(tree)
  }

  try {
    render()
    await flush()
    const loaded = render()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].auth, 'Bearer token-abc')
    assert.ok(loaded.some(n => textOf(n).includes('Joseline Banegas')))
    assert.ok(!loaded.some(n => textOf(n).includes('Carlos Ya Aceptado')))
    assert.ok(loaded.some(n => textOf(n).includes('1 pendiente')))

    const acceptBtn = loaded.find(n => n.type === 'button' && n.props?.title === 'Aceptar solicitud')
    assert.ok(acceptBtn)
    await acceptBtn.props.onClick()

    const afterAccept = render()
    assert.equal(calls[1].method, 'PATCH')
    assert.ok(calls[1].url.endsWith('/technicians/me/direct-requests/req-pendiente/accept'))
    assert.ok(afterAccept.some(n => textOf(n).includes('No tienes solicitudes directas pendientes en este momento.')))
    assert.ok(afterAccept.some(n => n.props?.role === 'status' && textOf(n).includes('Solicitud aceptada correctamente.')))
  } finally {
    cleanups.forEach(cleanup => cleanup())
    globalThis.fetch = originalFetch
  }
})

test('dashboard card renders friendly empty message when API returns no pending requests', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'token-abc' }
  const originalFetch = globalThis.fetch
  const cleanups = []

  globalThis.fetch = async () =>
    new Response(
      JSON.stringify([
        {
          id: 'req-rechazada',
          cliente_nombre: 'Cliente Antiguo',
          servicio_nombre: 'Pintura',
          fecha_hora: '2026-11-16T10:00:00Z',
          descripcion: 'Sala',
          estado: 'RECHAZADA',
          total_estimado: 500,
        },
      ]),
      { status: 200 },
    )

  const render = () => {
    harness.index = 0
    const tree = module.exports.default({ user })
    for (const effect of harness.effects.splice(0)) {
      const cleanup = effect()
      if (cleanup) cleanups.push(cleanup)
    }
    return nodes(tree)
  }

  try {
    render()
    await flush()
    const loaded = render()
    assert.ok(!loaded.some(n => textOf(n).includes('Cliente Antiguo')))
    assert.ok(loaded.some(n => textOf(n).includes('No tienes solicitudes directas pendientes en este momento.')))
    assert.ok(loaded.some(n => textOf(n).includes('Cuando recibas una nueva petición directa de un cliente')))
  } finally {
    cleanups.forEach(cleanup => cleanup())
    globalThis.fetch = originalFetch
  }
})
