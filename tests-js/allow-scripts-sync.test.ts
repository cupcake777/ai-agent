/**
 * Invariants tying pnpm's dependency build allowlist (root
 * ``pnpm-workspace.yaml``) to the lockfile it gates.
 *
 * Both allowlists are keyed by exact ``name@version``, so an entry silently
 * stops matching the moment that dependency is bumped.
 *
 * That has now bitten twice on Windows. ``get-windows`` was added to
 * ``apps/desktop`` without an allow entry, so its node-pre-gyp install script
 * never downloaded the win32 binding and ``hermes desktop`` died in
 * ``stage-native-deps``. In the same window, a CVE sweep moved Electron to
 * 40.10.6 and left the ``electron@40.10.2`` pin behind, blocking Electron's
 * own postinstall on any clean install.
 *
 * Two contracts keep an allowlist honest:
 *
 * - Every versioned pin names a version the lockfile actually resolves, so a
 *   dependency bump that orphans its pin fails here instead of in a user's
 *   build.
 * A bare-name key (no ``@version``) is a deliberate standing decision that
 * survives version bumps, which is how ``unicode-animations: false`` stays a
 * permanent denial.
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

import { load } from 'js-yaml'
import { describe, test } from 'vitest'

const REPO_ROOT = path.resolve(__dirname, '..')

function splitPin(key: string): { name: string; version: string } | null {
  // Scoped packages carry a leading @, so match the LAST @ as the separator.
  const match = key.match(/^(.+)@([^@]+)$/)

  return match ? { name: match[1], version: match[2] } : null
}

describe('root allowBuilds', () => {
  // SAFETY: pnpm writes both files; a missing section fails the assertions below.
  const workspace = load(fs.readFileSync(path.join(REPO_ROOT, 'pnpm-workspace.yaml'), 'utf-8')) as {
    allowBuilds?: Record<string, boolean>
    strictDepBuilds?: boolean
  }

  test('an unlisted dependency build fails the install', () => {
    assert.equal(workspace.strictDepBuilds, true, 'pnpm-workspace.yaml must set strictDepBuilds: true')
  })

  test('every versioned pin matches a version in the lockfile', () => {
    const lock = load(fs.readFileSync(path.join(REPO_ROOT, 'pnpm-lock.yaml'), 'utf-8')) as { packages?: Record<string, unknown> }
    const versions = new Map<string, Set<string>>()

    for (const key of Object.keys(lock.packages ?? {})) {
      const pin = splitPin(key)

      assert.ok(pin, `unreadable pnpm-lock.yaml package key ${key}`)
      versions.set(pin.name, (versions.get(pin.name) ?? new Set<string>()).add(pin.version))
    }

    const allow = workspace.allowBuilds ?? {}
    const stale: string[] = []

    assert.ok(Object.keys(allow).length > 0, 'pnpm-workspace.yaml has no allowBuilds decisions')

    for (const key of Object.keys(allow)) {
      const pin = splitPin(key)

      if (!pin) {
        continue
      }

      const installed = versions.get(pin.name)

      if (!installed?.has(pin.version)) {
        stale.push(`  "${key}" — lockfile resolves ${pin.name} to ${installed ? [...installed].join(', ') : '<nothing>'}`)
      }
    }

    assert.deepEqual(
      stale,
      [],
      `Stale allowBuilds entries in pnpm-workspace.yaml:\n${stale.join('\n')}\n` +
        "pnpm matches these by exact version, so each package's build fails the install " +
        'under strictDepBuilds. Update the pin to the installed version, or drop the ' +
        'entry if the dependency is gone.'
    )
  })
})
