import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
let roles = []
let adminTab
const result = await build({
  stdin: { contents: "export { default as AccountPage } from './src/pages/AccountPage'; export { default as AdminPage } from './src/pages/AdminPage'; export { default as AdminCatalog } from './src/components/AdminCatalog'; export { accountPath } from './src/lib/accountPath'", resolveDir: process.cwd(), loader:'tsx' },
  bundle:true, write:false, platform:'node', format:'cjs', packages:'external',
  loader:{ '.png':'dataurl', '.jpg':'dataurl', '.css':'empty' }, define:{ 'import.meta.env':'{}' },
  plugins:[{ name:'account-state', setup(build) {
    build.onResolve({ filter: /\/useAccount$/ }, () => ({ path:'test:account', external:true }))
  } }],
})
const module = { exports:{} }
new Function('require','module','exports',result.outputFiles[0].text)(id => {
  if (id==='react') return {
    ...require(id),
    useState:initial => initial === 'dossiers'
      ? [adminTab ?? initial, next => { adminTab = next }]
      : [initial, () => {}],
    useEffect() {},
  }
  if (id==='react-router-dom') return { ...require(id), useNavigate:() => () => {}, useLocation:() => ({ state:null }) }
  if (id==='test:account') return { useAccount:() => ({ state:{ status:'ready', profile:{ roles, nombre_completo:'Admin', estado_identidad:'PENDIENTE' } }, retry() {}, updateProfile() {} }) }
  return require(id)
},module,module.exports)

test('admin-only and mixed-role accounts enter administration directly without identity prerequisites', () => {
  for (const assigned of [['ADMIN'],['ADMIN','CLIENTE'],['TECNICO','ADMIN'],['CLIENTE','TECNICO','ADMIN']]) {
    roles = assigned
    assert.equal(module.exports.accountPath(roles), '/admin')
    for (const space of [undefined,'CLIENTE','TECNICO']) {
      const page = module.exports.AccountPage({ user:{ uid:'admin' }, space })
      assert.equal(page.props.to, '/admin')
      assert.equal(page.props.replace, true)
    }
  }
  assert.equal(module.exports.accountPath(['CLIENTE']), '/cliente')
  assert.equal(module.exports.accountPath(['TECNICO']), '/tecnico')
  assert.equal(module.exports.accountPath(['CLIENTE','TECNICO']), '/mi-cuenta')
})

function findElement(node, matches) {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findElement(child, matches)
      if (found) return found
    }
  } else if (node?.props) {
    if (matches(node)) return node
    return findElement(node.props.children, matches)
  }
}

test('admin can open the catalog and regular accounts cannot access it', () => {
  const { AdminPage, AdminCatalog } = module.exports
  const user = { uid: 'admin' }
  const catalogTab = node => node.props.role === 'tab' &&
    node.props.children.includes(' Categorías y parámetros')
  const catalogPanel = node => node.type === AdminCatalog

  roles = ['ADMIN']
  adminTab = undefined
  const tab = findElement(AdminPage({ user }), catalogTab)
  assert.equal(tab.props['aria-selected'], false)
  tab.props.onClick()

  const page = AdminPage({ user })
  assert.equal(findElement(page, catalogTab).props['aria-selected'], true)
  assert.equal(findElement(page, catalogPanel).props.user, user)

  roles = ['CLIENTE']
  const restrictedPage = AdminPage({ user })
  assert.equal(findElement(restrictedPage, catalogTab), undefined)
  assert.equal(findElement(restrictedPage, catalogPanel), undefined)
})
