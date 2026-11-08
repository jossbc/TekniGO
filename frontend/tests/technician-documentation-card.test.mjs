import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
let harness
const result = await build({
  entryPoints: ['src/components/TechnicianDocumentationCard.tsx'], bundle: true, write: false,
  platform: 'node', format: 'cjs', packages: 'external',
  loader: { '.png': 'dataurl' }, define: { 'import.meta.env': '{}' },
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(id => {
  if (id === 'react') return { ...React,
    useState(initial) {
      const index = harness.index++
      if (!(index in harness.state)) harness.state[index] = typeof initial === 'function' ? initial() : initial
      return [harness.state[index], value => { harness.state[index] = typeof value === 'function' ? value(harness.state[index]) : value }]
    },
    useRef(initial) {
      const index = harness.index++
      if (!(index in harness.state)) harness.state[index] = { current: initial }
      return harness.state[index]
    },
    useEffect(effect, deps) {
      const index = harness.index++
      const previous = harness.state[index]
      if (!previous || deps.some((dep, i) => dep !== previous[i])) harness.effects.push(effect)
      harness.state[index] = deps
    },
  }
  return require(id)
}, module, module.exports)

const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...React.Children.toArray(tree.props?.children).flatMap(nodes)]
const flush = () => new Promise(resolve => setImmediate(resolve))

test('documentation card refreshes from pending to verified on focus and removes upload prompt', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'token' }
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  globalThis.window = new EventTarget()
  globalThis.document = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  const cleanups = []
  let verified = false
  let calls = 0
  globalThis.fetch = async url => {
    calls++
    assert.ok(url.endsWith('/technicians/me/activation-status'))
    return new Response(JSON.stringify({estado_verificacion: verified ? 'VERIFICADO' : 'EN_REVISION', requisitos_pendientes: []}))
  }
  const render = () => {
    harness.index = 0
    const tree = nodes(module.exports.default({user}))
    for (const effect of harness.effects.splice(0)) { const cleanup = effect(); if (cleanup) cleanups.push(cleanup) }
    return tree
  }
  try {
    assert.ok(render().some(node => node.props.children === 'Consultando documentación.'))
    await flush()
    assert.ok(render().some(node => Array.isArray(node.props.children) && node.props.children.includes('Revisar documentos y antecedentes')))
    verified = true
    window.dispatchEvent(new Event('focus'))
    await flush()
    const approved = render()
    assert.ok(approved.some(node => Array.isArray(node.props.children) && node.props.children.includes('Verificado')))
    assert.ok(approved.some(node => Array.isArray(node.props.children) && node.props.children.includes('Ver documentos aprobados')))
    assert.ok(!approved.some(node => Array.isArray(node.props.children) && node.props.children.includes('Revisar documentos y antecedentes')))
    assert.equal(calls, 2)
    cleanups.splice(0).forEach(cleanup => cleanup())
    window.dispatchEvent(new Event('focus'))
    assert.equal(calls, 2)
  } finally {
    cleanups.forEach(cleanup => cleanup())
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
  }
})
