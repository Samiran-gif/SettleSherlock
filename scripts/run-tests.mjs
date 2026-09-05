/**
 * Bundles the TS tests with esbuild (already vendored inside Vite, so no extra
 * dependency) and runs them under node:test.
 *
 * A bundle step is needed because the data layer imports CSV via Vite's `?raw`
 * suffix, which plain node cannot resolve.
 */
import { build } from 'esbuild'
import { spawn } from 'node:child_process'
import { readFileSync, readdirSync, rmSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const outdir = fileURLToPath(new URL('../.test-build/', import.meta.url))

rmSync(outdir, { recursive: true, force: true })
mkdirSync(outdir, { recursive: true })

/** Resolves Vite's `?raw` imports to the file's text contents. */
const rawLoader = {
  name: 'raw-loader',
  setup(b) {
    b.onResolve({ filter: /\?raw$/ }, (args) => ({
      path: fileURLToPath(new URL(args.path.replace(/\?raw$/, ''), `file://${args.resolveDir}/`)),
      namespace: 'raw',
    }))
    b.onLoad({ filter: /.*/, namespace: 'raw' }, (args) => ({
      contents: readFileSync(args.path, 'utf8'),
      loader: 'text',
    }))
  },
}

const entryPoints = readdirSync(`${root}tests`)
  .filter((f) => f.endsWith('.test.ts'))
  .map((f) => `${root}tests/${f}`)
if (entryPoints.length === 0) {
  console.error('no test files found')
  process.exit(1)
}

await build({
  entryPoints,
  outdir,
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node20',
  jsx: 'automatic',
  sourcemap: 'inline',
  outExtension: { '.js': '.mjs' },
  plugins: [rawLoader],
  // node:test and React must stay external: React's server build is CJS and
  // dynamically requires 'stream', which cannot survive an ESM bundle.
  external: ['node:*', 'react', 'react-dom', 'react-dom/server', 'react/jsx-runtime'],
  logLevel: 'warning',
})

const child = spawn(process.execPath, ['--test', outdir], { stdio: 'inherit' })
child.on('exit', (code) => process.exit(code ?? 1))
