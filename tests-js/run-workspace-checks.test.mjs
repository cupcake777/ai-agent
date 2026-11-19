import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { afterEach, expect, test } from 'vitest'

const repo = resolve(import.meta.dirname, '..')
const runner = join(repo, '.github/scripts/run-workspace-checks.mjs')
const scratch = []

afterEach(() => {
  for (const path of scratch.splice(0)) rmSync(path, { recursive: true, force: true })
})

test('environment concurrency cap serializes top-level workspace checks', () => {
  const root = mkdtempSync(join(tmpdir(), 'workspace-checks-'))
  scratch.push(root)
  const bin = join(root, 'bin')
  const state = join(root, 'state')
  mkdirSync(bin)
  mkdirSync(state)

  const projects = []
  for (const name of ['one', 'two']) {
    const path = join(root, name)
    mkdirSync(path)
    writeFileSync(join(path, 'package.json'), JSON.stringify({ name, scripts: { check: 'fake' } }))
    projects.push({ path })
  }

  const pnpm = join(bin, 'pnpm')
  writeFileSync(
    pnpm,
    `#!/bin/sh\nset -eu\nif [ "\${1-}" = ls ]; then printf '%s\\n' "$PROJECTS_JSON"; exit 0; fi\nif mkdir "$STATE/active" 2>/dev/null; then\n  sleep 0.2\n  rmdir "$STATE/active"\nelse\n  : > "$STATE/overlap"\n  exit 9\nfi\n`,
  )
  chmodSync(pnpm, 0o755)

  const result = spawnSync(process.execPath, [runner], {
    cwd: root,
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${bin}:${process.env.PATH}`,
      PROJECTS_JSON: JSON.stringify(projects),
      STATE: state,
      HERMES_JS_CHECK_CONCURRENCY: '1',
    },
  })

  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0)
  expect(result.stdout).toContain('running 2 checks, up to 1 at a time:')
  expect(result.stdout).toContain('all 2 checks passed')
  expect(existsSync(join(state, 'overlap'))).toBe(false)
})
