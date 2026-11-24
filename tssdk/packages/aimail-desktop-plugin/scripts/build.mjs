/**
 * Bundle the plugin entry into one CommonJS file.
 *
 * The host evaluates `main.js` as CommonJS and expects `module.exports` with
 * `onLoad` / `onUnload`. `agent-aimail` ships ESM, so the bundle is the only
 * shape that satisfies both.
 *
 * `dist/` is the package root: `manifest.json` sits beside `main.js`, which is
 * what `scripts/plugin-pack.mjs` zips into a `.piplug`.
 */
import { build } from 'esbuild'
import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const out = join(root, 'dist')

const pkg = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'))
const manifest = JSON.parse(await readFile(join(root, 'manifest.json'), 'utf8'))
if (pkg.version !== manifest.version) {
  console.error(
    `build: manifest.version (${manifest.version}) must equal package.json version (${pkg.version})`,
  )
  process.exit(1)
}

await rm(out, { recursive: true, force: true })
await mkdir(out, { recursive: true })

await build({
  entryPoints: [join(root, 'src/main.ts')],
  outfile: join(out, manifest.main),
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node20',
  // Bundled, not external: the plugin package is installed without node_modules.
  packages: 'bundle',
  legalComments: 'none',
  logLevel: 'info',
})

await cp(join(root, 'manifest.json'), join(out, 'manifest.json'))
console.log(`build: ${manifest.id}@${manifest.version} → ${manifest.main}`)

// The host `require`s main.js, but this package is `type: module`: without a
// nested marker Node would parse the bundle as ESM and `module.exports` would
// never reach the host.
await writeFile(join(out, 'package.json'), `${JSON.stringify({ type: 'commonjs' }, null, 2)}\n`)
