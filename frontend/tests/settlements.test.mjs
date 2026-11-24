import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({
  stdin: { contents: "export * from './src/lib/settlements'", resolveDir: process.cwd(), loader: 'ts' },
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  define: { 'import.meta.env': '{}' },
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
const { validateSettlementInput, validateDecisionInput, formatLempiras, settlementStatuses } = module.exports

const rules = { formatos_permitidos: ['image/jpeg', 'image/png', 'image/webp'], tamano_maximo_bytes: 5 * 1024 * 1024, formatos_texto: 'JPG, PNG o WEBP' }
const png = { type: 'image/png', size: 1000 }

test('valid settlement input passes client validation', () => {
  assert.equal(validateSettlementInput({ importe: '100.50', referencia: 'BAC-1001', file: png }, rules), null)
})

test('invalid amounts, references and files are rejected before upload', () => {
  const bad = [
    { importe: '0', referencia: 'BAC-1001', file: png },
    { importe: '10.999', referencia: 'BAC-1001', file: png },
    { importe: 'abc', referencia: 'BAC-1001', file: png },
    { importe: '10', referencia: 'x', file: png },
    { importe: '10', referencia: 'BAC#1', file: png },
    { importe: '10', referencia: 'BAC-1001', file: null },
    { importe: '10', referencia: 'BAC-1001', file: { type: 'application/pdf', size: 10 } },
    { importe: '10', referencia: 'BAC-1001', file: { type: 'image/png', size: 6 * 1024 * 1024 } },
    { importe: '10', referencia: 'BAC-1001', file: { type: 'image/png', size: 0 } },
  ]
  for (const input of bad) assert.notEqual(validateSettlementInput(input, rules), null, JSON.stringify(input))
})

test('amounts are shown in lempiras with cents and all states have labels', () => {
  assert.match(formatLempiras('1250.5'), /^L\s1[,.\s]?250[.,]50$/)
  assert.deepEqual(Object.keys(settlementStatuses).sort(), ['APROBADA', 'EN_CONCILIACION', 'PENDIENTE', 'RECHAZADA'])
})

test('admin decision validation requires verified amount to approve and observation to reject', () => {
  assert.equal(validateDecisionInput({ decision: 'APROBAR', importe_verificado: '150.25', observacion: '' }), null)
  assert.notEqual(validateDecisionInput({ decision: 'APROBAR', importe_verificado: '0', observacion: '' }), null)
  assert.notEqual(validateDecisionInput({ decision: 'APROBAR', importe_verificado: '10.999', observacion: '' }), null)
  assert.notEqual(validateDecisionInput({ decision: 'RECHAZAR', importe_verificado: '', observacion: '   ' }), null)
  assert.equal(validateDecisionInput({ decision: 'RECHAZAR', importe_verificado: '', observacion: 'Comprobante borroso' }), null)
})
