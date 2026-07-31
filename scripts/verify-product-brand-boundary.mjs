#!/usr/bin/env node

import { readFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { execFile } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { promisify } from 'node:util'

import { referenceProvenance } from './reference-provenance.mjs'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const REPOSITORY_ROOT = path.resolve(SCRIPT_DIR, '..')
const executeFile = promisify(execFile)
const SOURCE_PROVENANCE = 'references/provenance/source.json'
const SOURCE_CLASSIFICATION_INPUT = 'docs/migration/reference-ui-delta-classifications.json'
const SOURCE_EVIDENCE_LEDGERS = new Set([
  'docs/migration/reference-source-ledger.json',
  'docs/migration/reference-ui-delta-ledger.json',
  SOURCE_CLASSIFICATION_INPUT,
])

function escaped(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export const legacyProductPattern = new RegExp(
  referenceProvenance.legacyProductTerms.map(escaped).join('|'),
  'i',
)

function isAllowedReferencePath(relativePath) {
  return (
    relativePath === 'NOTICE'
    || relativePath === 'LICENSE-APACHE-2.0.txt'
    || relativePath.startsWith('references/upstream/')
    || relativePath === SOURCE_PROVENANCE
    || relativePath.startsWith('references/provenance/')
    || (relativePath.startsWith('references/') && relativePath.includes('/vendor/'))
    || (relativePath.startsWith('references/') && relativePath.includes('/node_modules/'))
  )
}

function sanitizeImmutableSourcePaths(relativePath, parsed) {
  if (!SOURCE_EVIDENCE_LEDGERS.has(relativePath)) return parsed
  const copy = structuredClone(parsed)
  if (Array.isArray(copy.files)) {
    copy.files.forEach((row) => {
      if (row && typeof row === 'object') {
        if ('path' in row) row.path = '<immutable-source-path>'
        if ('previousPath' in row) row.previousPath = '<immutable-source-path>'
      }
    })
  }
  if (
    relativePath === SOURCE_CLASSIFICATION_INPUT
    && copy.classifications
    && typeof copy.classifications === 'object'
    && !Array.isArray(copy.classifications)
  ) {
    copy.classifications = Object.fromEntries(
      Object.values(copy.classifications).map((value, index) => [
        `<immutable-source-path-${index}>`,
        value,
      ]),
    )
  }
  return copy
}

function lineNumber(text, index) {
  return text.slice(0, index).split('\n').length
}

export function inspectProductBoundaryFile(relativePath, text) {
  if (isAllowedReferencePath(relativePath)) return []
  const normalized = SOURCE_EVIDENCE_LEDGERS.has(relativePath)
    ? JSON.stringify(sanitizeImmutableSourcePaths(relativePath, JSON.parse(text)))
    : text
  const violations = []
  for (const match of normalized.matchAll(new RegExp(legacyProductPattern.source, 'gi'))) {
    violations.push({
      path: relativePath,
      line: lineNumber(normalized, match.index ?? 0),
      term: match[0],
    })
  }
  return violations
}

export async function repositoryFiles(root = REPOSITORY_ROOT) {
  const { stdout } = await executeFile('git', ['-C', root, 'ls-files', '-co', '--exclude-standard', '-z'], {
    encoding: 'buffer',
  })
  return stdout.toString('utf8').split('\0').filter(Boolean).sort()
}

export async function findProductBoundaryViolations(root = REPOSITORY_ROOT) {
  const files = await repositoryFiles(root)
  const violations = []
  for (const relativePath of files) {
    if (isAllowedReferencePath(relativePath)) continue
    const absolutePath = path.join(root, relativePath)
    const content = await readFile(absolutePath).catch(() => null)
    if (content === null) continue
    if (legacyProductPattern.test(relativePath)) {
      violations.push({ path: relativePath, line: 0, term: '<path>' })
    }
    legacyProductPattern.lastIndex = 0
    if (content.includes(0)) continue
    try {
      violations.push(...inspectProductBoundaryFile(relativePath, content.toString('utf8')))
    } catch (error) {
      violations.push({ path: relativePath, line: 0, term: `invalid-source-evidence:${error instanceof Error ? error.message : String(error)}` })
    }
  }
  return violations
}

async function main() {
  const violations = await findProductBoundaryViolations()
  if (violations.length === 0) {
    process.stdout.write('product brand boundary check passed\n')
    return
  }
  for (const violation of violations) {
    const location = violation.line > 0 ? `:${violation.line}` : ''
    process.stderr.write(`${violation.path}${location}: legacy product boundary violation (${violation.term})\n`)
  }
  process.exitCode = 1
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  })
}
