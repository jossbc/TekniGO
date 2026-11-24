import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({
  stdin: { contents: "export * from './src/lib/payouts'", resolveDir: process.cwd(), loader: 'ts' },
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  define: { 'import.meta.env': '{}' },
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
const { validateBankAccountInput, validatePayoutRequestInput, validatePayoutDecisionInput, payoutStatuses } = module.exports

const baseSummary = {
  cuenta_bancaria: {
    id: 'acc-1',
    banco: 'BAC Credomatic',
    tipo_cuenta: 'AHORRO',
    titular_nombre: 'Juan Pérez',
    ultimos_cuatro: '1234',
    cuenta_enmascarada: '****1234',
    actualizado_en: null,
  },
  deuda_vigente: '0.00',
  minimo_retiro: '200.00',
  saldo_libre: '500.00',
}

test('bank account validation accepts valid digits and rejects malformed input', () => {
  assert.equal(
    validateBankAccountInput({
      banco: 'BAC Credomatic',
      tipo_cuenta: 'AHORRO',
      titular_nombre: 'Juan Pérez',
      numero_cuenta: '7412-8890-1234',
    }),
    null,
  )
  for (const bad of [
    { banco: 'A', tipo_cuenta: 'AHORRO', titular_nombre: 'Juan Pérez', numero_cuenta: '741288901234' },
    { banco: 'BAC', tipo_cuenta: 'OTRA', titular_nombre: 'Juan Pérez', numero_cuenta: '741288901234' },
    { banco: 'BAC', tipo_cuenta: 'AHORRO', titular_nombre: 'Jo', numero_cuenta: '741288901234' },
    { banco: 'BAC', tipo_cuenta: 'AHORRO', titular_nombre: 'Juan Pérez', numero_cuenta: '1234567' },
    { banco: 'BAC', tipo_cuenta: 'AHORRO', titular_nombre: 'Juan Pérez', numero_cuenta: 'ABCD12345678' },
  ]) {
    assert.notEqual(validateBankAccountInput(bad), null, JSON.stringify(bad))
  }
})

test('payout validation enforces minimum L 200.00 (rejects 199.99, admits 200.00), zero debt and available balance', () => {
  assert.notEqual(validatePayoutRequestInput('199.99', baseSummary), null)
  assert.equal(validatePayoutRequestInput('200.00', baseSummary), null)
  assert.equal(validatePayoutRequestInput('500.00', baseSummary), null)
  assert.notEqual(validatePayoutRequestInput('500.01', baseSummary), null)
  assert.notEqual(validatePayoutRequestInput('200.001', baseSummary), null)
  assert.notEqual(validatePayoutRequestInput('200.00', { ...baseSummary, deuda_vigente: '0.01' }), null)
  assert.notEqual(validatePayoutRequestInput('200.00', { ...baseSummary, cuenta_bancaria: null }), null)
})

test('all payout states have human-readable labels', () => {
  assert.deepEqual(Object.keys(payoutStatuses).sort(), ['PAGADA', 'PENDIENTE', 'RECHAZADA'])
})

test('admin payout decision requires valid transfer reference to confirm and reason to reject', () => {
  assert.equal(
    validatePayoutDecisionInput({ decision: 'CONFIRMAR', referencia_transferencia: 'TRF-2026-01', motivo_rechazo: '' }),
    null,
  )
  assert.notEqual(
    validatePayoutDecisionInput({ decision: 'CONFIRMAR', referencia_transferencia: 'ab', motivo_rechazo: '' }),
    null,
  )
  assert.notEqual(
    validatePayoutDecisionInput({ decision: 'RECHAZAR', referencia_transferencia: '', motivo_rechazo: '   ' }),
    null,
  )
  assert.equal(
    validatePayoutDecisionInput({ decision: 'RECHAZAR', referencia_transferencia: '', motivo_rechazo: 'Cuenta cerrada' }),
    null,
  )
})
