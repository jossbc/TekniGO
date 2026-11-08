import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)

test('mock phone verification is opt-in for development and cannot activate in production', async () => {
  for (const [dev, flag, expected] of [[true, 'true', true], [true, 'false', false], [false, 'true', false], [false, undefined, false]]) {
    const auth = { settings: {} }
    const result = await build({
      entryPoints: ['src/lib/firebase.ts'], bundle: true, write: false,
      platform: 'node', format: 'cjs', packages: 'external',
      define: { 'import.meta.env': JSON.stringify({
        DEV: dev, VITE_FIREBASE_PHONE_TEST_MODE: flag,
        VITE_FIREBASE_API_KEY: 'test', VITE_FIREBASE_AUTH_DOMAIN: 'test.example',
        VITE_FIREBASE_PROJECT_ID: 'test', VITE_FIREBASE_APP_ID: 'test',
      }) },
    })
    const module = { exports: {} }
    new Function('require', 'module', 'exports', result.outputFiles[0].text)(id => {
      if (id === 'firebase/app') return { initializeApp: config => ({ options: config }) }
      if (id === 'firebase/auth') return { getAuth: () => auth }
      return require(id)
    }, module, module.exports)
    assert.equal(module.exports.phoneTestMode, expected)
    assert.equal(auth.settings.appVerificationDisabledForTesting, expected)
  }
})
