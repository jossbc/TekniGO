import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
let harness
const result = await build({
  entryPoints: ['src/components/TechnicianVerificationForm.tsx'], bundle: true, write: false,
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

test('technical identity loads safely, retries the same version and requires rejected files to be replaced', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'test-token' }
  const updates = []
  function render() {
    harness.index = 0
    const tree = nodes(module.exports.default({ user, onStatusChange: state => updates.push(state) }))
    for (const effect of harness.effects.splice(0)) effect()
    return tree
  }
  const previousFetch = globalThis.fetch
  const calls = []
  const documents = ['DNI_FRONTAL','DNI_REVERSO','SELFIE','ANTECEDENTES_POLICIALES','ANTECEDENTES_PENALES']
    .map((tipo, index) => ({ id: String(index), tipo, estado: 'PENDIENTE' }))
  const pending = { estado_verificacion:'EN_REVISION', estado_identidad:'EN_REVISION', solicitud_id:'current-version',
    procesamiento:{ estado:'ERROR', observacion:'No se pudo completar la evaluación' }, documentos:documents }
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (calls.length === 1) return new Response(JSON.stringify(pending))
    assert.ok(url.endsWith('/technicians/me/identity-verification'))
    assert.equal(options.method, 'POST')
    assert.deepEqual(JSON.parse(options.body), { solicitud_id:'current-version' })
    return new Response(JSON.stringify({ ...pending, estado_verificacion:'RECHAZADO', estado_identidad:'RECHAZADO',
      procesamiento:{ estado:'RECHAZADO', observacion:'Los datos del DNI no coinciden' },
      documentos:documents.map(doc => ({ ...doc, estado:doc.tipo.startsWith('ANTECEDENTES') ? 'PENDIENTE' : 'RECHAZADO' })),
    }))
  }
  try {
    const loading = render()
    assert.ok(loading.some(node => node.props?.message?.startsWith('Consultando tu expediente')))
    assert.ok(!loading.some(node => node.type === 'form'))
    await flush()
    const pendingTree = render()
    const retry = pendingTree.find(node => node.props?.onClick && node.props.children === 'Reintentar evaluación')
    assert.ok(retry)
    await retry.props.onClick()
    const rejectedTree = render()
    assert.deepEqual(updates, ['RECHAZADO'])
    assert.equal(calls.length, 2)
    assert.ok(rejectedTree.some(node => node.type === 'form'), 'a rejected version allows corrected documents')
    await rejectedTree.find(node => node.type === 'form').props.onSubmit({ preventDefault() {} })
    assert.equal(calls.length, 2, 'rejected identity files cannot be reused without replacements')
  } finally { globalThis.fetch = previousFetch }
})

test('technical screen shows administrative identity approval while backgrounds remain pending', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'test-token' }
  const previousFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({
    estado_identidad: 'VERIFICADO', estado_verificacion: 'PENDIENTE', solicitud_id: 'approved-client',
    procesamiento: { estado: 'VERIFICADO', observacion: 'Aprobación manual', proceso_responsable: 'REVISION_ADMINISTRATIVA_IDENTIDAD' },
    documentos: ['DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'].map(tipo => ({ id: tipo, tipo, estado: 'APROBADO' })),
    requisitos_pendientes: [{ codigo: 'ANTECEDENTES_POLICIALES', nombre: 'Antecedentes policiales', estado: 'FALTANTE' }],
  }))
  const render = () => {
    harness.index = 0
    const tree = nodes(module.exports.default({ user }))
    for (const effect of harness.effects.splice(0)) effect()
    return tree
  }
  try {
    render(); await flush()
    const tree = render()
    assert.ok(tree.some(node => node.props.children === 'VERIFICADO: Aprobación manual'))
    assert.ok(tree.some(node => node.props.children === 'Este es el resultado de la revisión administrativa de tu identidad.'))
    assert.ok(!tree.some(node => node.props.children === 'Reintentar evaluación'))
  } finally { globalThis.fetch = previousFetch }
})

test('failed dossier reads never show an empty upload form and can be retried safely', async () => {
  harness = { index: 0, state: [], effects: [] }
  const user = { getIdToken: async () => 'test-token' }
  const previousFetch = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => ++calls === 1 ? new Response(JSON.stringify({detail:'Servicio temporalmente no disponible'}), {status:503}) : new Response(JSON.stringify({
    estado_verificacion:'EN_REVISION', estado_identidad:'VERIFICADO', solicitud_id:'existing',
    documentos:['DNI_FRONTAL','DNI_REVERSO','SELFIE'].map(tipo => ({id:tipo,tipo,estado:'APROBADO'})),
  }))
  const render = () => {
    harness.index = 0
    const tree = nodes(module.exports.default({user}))
    for (const effect of harness.effects.splice(0)) effect()
    return tree
  }
  try {
    render(); await flush()
    const failed = render()
    assert.ok(!failed.some(node => node.type === 'form'))
    const retry = failed.find(node => node.props.children === 'Reintentar carga del expediente')
    assert.ok(retry)
    retry.props.onClick(); render(); await flush()
    const loaded = render()
    assert.ok(loaded.some(node => node.type === 'form'), 'missing backgrounds remain uploadable even with stale EN_REVISION state')
    assert.equal(loaded.filter(node => node.type === 'input' && node.props.type === 'file').length, 2, 'approved DNI/selfie do not reappear as upload requirements')
    assert.ok(!loaded.some(node => node.type === 'h3' && node.props.children === 'Solicitud en proceso de revisión'))
  } finally { globalThis.fetch = previousFetch }
})
