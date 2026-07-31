#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { spawn } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { PROVENANCE_PATH, referenceProvenance } from './reference-provenance.mjs'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const REPOSITORY_ROOT = path.resolve(SCRIPT_DIR, '..')
export const DEFAULT_BASE_REVISION = referenceProvenance.uiBaseRevision
export const DEFAULT_TARGET_REVISION = referenceProvenance.revision
const DEFAULT_REPOSITORY = '/tmp/opsia-upstream-verify'
const DEFAULT_INVENTORY = path.join(REPOSITORY_ROOT, 'docs', 'spec', 'frontend', 'reference-feature-inventory.md')
const DEFAULT_OUTPUT = path.join(REPOSITORY_ROOT, 'docs', 'migration', 'reference-ui-delta-ledger.json')
const DEFAULT_FEATURE_LEDGER = path.join(REPOSITORY_ROOT, 'docs', 'migration', 'reference-feature-ledger.json')
const DEFAULT_CLASSIFICATION_INPUT = path.join(
  REPOSITORY_ROOT,
  'docs',
  'migration',
  'reference-ui-delta-classifications.json',
)
const PROVENANCE_REFERENCE = path.relative(REPOSITORY_ROOT, PROVENANCE_PATH).split(path.sep).join('/')
const DEFAULT_SCOPE = ['web', 'packages/k8s-ui']

const DELTA_LEDGER_SCHEMA_VERSION = 3
const CHANGE_STATUSES = new Set(['A', 'M', 'D', 'R'])
const TRANSPORTS = new Set(['sse', 'ws', 'ndjson', 'fetch_sse', 'poll', 'none'])
const CLASSIFICATIONS = new Set(['pending', 'classified'])
const IMPLEMENTATION_STATES = new Set(['in_progress', 'blocked'])
const SOURCE_KEY = /^upstream-ui:[a-z0-9-]+:[a-z0-9-]+:[a-z0-9-]+:v[1-9][0-9]*$/
const REVISION = /^[0-9a-f]{40}$/
const BLOB = /^[0-9a-f]{40}$/
const SHA256 = /^[0-9a-f]{64}$/
const BACKEND_CONTRACT = /^(?:domains|packages)\.[a-z0-9_.]+$/
// A test plan belongs to a product domain, not to a particular first migration
// wave.  Keeping this deliberately generic lets the immutable source ledger
// make the same proof requirement for applications, topology, GitOps, and
// every later port instead of quietly making Timeline the only classifiable
// surface.
const PLANNED_TEST_ID = /^[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+$/
const FILE_LEVEL_INTERACTION_EVIDENCE_KEYS = [
  'sourceKey',
  'legacyContractIds',
  'symbol',
  'interaction',
  'transport',
  'realtime',
  'motion',
]

function cloned(value) {
  return JSON.parse(JSON.stringify(value))
}

function normalizedPath(value) {
  return String(value).split(path.sep).join('/')
}

function sortRows(left, right) {
  return left.path.localeCompare(right.path) || left.status.localeCompare(right.status)
}

function sourceFile(value, label) {
  if (!value || !BLOB.test(value.blobId ?? '') || !SHA256.test(value.sha256 ?? '')) {
    throw new Error(`${label} source evidence is missing blobId or sha256`)
  }
  return { blobId: value.blobId, sha256: value.sha256 }
}

function sourceFileFromMap(files, filePath, label) {
  const value = files instanceof Map ? files.get(filePath) : files?.[filePath]
  return sourceFile(value, `${label} ${filePath}`)
}

function blankDeltaRow(change, baseFiles, targetFiles) {
  const status = change.status
  const filePath = normalizedPath(change.path)
  const previousPath = change.previousPath ? normalizedPath(change.previousPath) : null
  if (!CHANGE_STATUSES.has(status)) throw new Error(`unsupported source change status: ${status}`)
  if (!filePath || filePath.startsWith('/') || filePath.includes('..')) throw new Error(`invalid source path: ${filePath}`)
  if (status === 'R' && !previousPath) throw new Error(`rename ${filePath} requires previousPath`)
  if (status !== 'R' && previousPath !== null) throw new Error(`${status} ${filePath} must not define previousPath`)

  const needsBase = status === 'M' || status === 'D' || status === 'R'
  const needsTarget = status === 'A' || status === 'M' || status === 'R'
  return {
    status,
    previousPath,
    path: filePath,
    base: needsBase ? sourceFileFromMap(baseFiles, previousPath ?? filePath, 'base') : null,
    target: needsTarget ? sourceFileFromMap(targetFiles, filePath, 'target') : null,
    classification: 'pending',
    interactions: [],
  }
}

export function resolveDeltaLedgerOptions(options = {}) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new Error('source delta options must be an object')
  }
  const repository = options.repository ?? DEFAULT_REPOSITORY
  return {
    ...options,
    repository,
    baseRevision: options.baseRevision ?? DEFAULT_BASE_REVISION,
    targetRevision: options.targetRevision ?? DEFAULT_TARGET_REVISION,
    sourceProvenance: options.sourceProvenance ?? PROVENANCE_REFERENCE,
    scope: options.scope ?? DEFAULT_SCOPE,
    classificationInput: options.classificationInput ?? (
      repository === DEFAULT_REPOSITORY ? DEFAULT_CLASSIFICATION_INPUT : null
    ),
  }
}

function applyClassifications(files, classifications) {
  if (!classifications) return
  const rowsByPath = new Map(files.map((row) => [row.path, row]))
  for (const [filePath, classification] of Object.entries(classifications)) {
    const row = rowsByPath.get(filePath)
    if (!row) throw new Error(`classification input references a source path absent from the git delta: ${filePath}`)
    row.classification = classification.classification
    row.interactions = cloned(classification.interactions)
  }
}

export function buildDeltaLedger({
  baseRevision,
  targetRevision,
  sourceProvenance = PROVENANCE_REFERENCE,
  scope = DEFAULT_SCOPE,
  changes,
  baseFiles,
  targetFiles,
  classifications = null,
}) {
  if (!REVISION.test(baseRevision ?? '')) throw new Error('baseRevision must be a 40-character lowercase hexadecimal revision')
  if (!REVISION.test(targetRevision ?? '')) throw new Error('targetRevision must be a 40-character lowercase hexadecimal revision')
  if (!Array.isArray(changes)) throw new Error('changes must be an array')
  const files = changes.map((change) => blankDeltaRow(change, baseFiles, targetFiles)).sort(sortRows)
  applyClassifications(files, classifications)
  const statusCounts = Object.fromEntries([...CHANGE_STATUSES].map((status) => [status, files.filter((row) => row.status === status).length]))
  const ledger = {
    schemaVersion: DELTA_LEDGER_SCHEMA_VERSION,
    sourceProvenance,
    baseRevision,
    targetRevision,
    scope: [...scope].map(normalizedPath),
    fileCount: files.length,
    pendingCount: files.filter((row) => row.classification === 'pending').length,
    statusCounts,
    files,
  }
  const errors = validateDeltaLedger(ledger)
  if (errors.length > 0) throw new Error(`source delta ledger validation failed:\n${errors.join('\n')}`)
  return ledger
}

function validateEvidence(value, label, errors) {
  if (!value || typeof value !== 'object') {
    errors.push(`${label}: source evidence is required`)
    return
  }
  if (!BLOB.test(value.blobId ?? '')) errors.push(`${label}: blobId must be a 40-character lowercase hexadecimal value`)
  if (!SHA256.test(value.sha256 ?? '')) errors.push(`${label}: sha256 must be a 64-character lowercase hexadecimal value`)
}

function validatePath(value, label, errors) {
  if (!value || value.startsWith('/') || value.includes('..') || value !== normalizedPath(value)) {
    errors.push(`${label}: path must be a normalized relative path`)
  }
}

function validateUniqueStrings(values, label, errors, { pattern = null, minimum = 0 } = {}) {
  if (!Array.isArray(values) || values.length < minimum) {
    errors.push(`${label}: must contain at least ${minimum} item${minimum === 1 ? '' : 's'}`)
    return
  }
  const seen = new Set()
  values.forEach((value, index) => {
    if (typeof value !== 'string' || !value.trim() || (pattern && !pattern.test(value))) {
      errors.push(`${label}[${index}]: is invalid`)
      return
    }
    if (seen.has(value)) errors.push(`${label}[${index}]: is duplicated: ${value}`)
    else seen.add(value)
  })
}

function validateOpsiaPort(port, label, errors, { knownOpsiaDestinations = null, knownPlannedTestIds = null } = {}) {
  if (!port || typeof port !== 'object' || Array.isArray(port)) {
    errors.push(`${label}: opsiaPort is required`)
    return
  }
  const expectedKeys = new Set([
    'destinations',
    'requiredBackendContracts',
    'plannedTestIds',
    'state',
    'blockedReason',
    'rationale',
  ])
  for (const key of Object.keys(port)) {
    if (!expectedKeys.has(key)) errors.push(`${label}: opsiaPort.${key} is not supported`)
  }
  validateUniqueStrings(port.destinations, `${label}: opsiaPort.destinations`, errors, { minimum: 1 })
  if (Array.isArray(port.destinations)) {
    port.destinations.forEach((destination, index) => {
      validatePath(destination, `${label}: opsiaPort.destinations[${index}]`, errors)
      if (!/^(?:desktop|frontend|src|tests)\//.test(destination ?? '')) {
        errors.push(`${label}: opsiaPort.destinations[${index}]: must target a current Opsia product or test path`)
      }
      if (knownOpsiaDestinations && !knownOpsiaDestinations.has(destination)) {
        errors.push(`${label}: opsiaPort.destinations[${index}]: is not a known Opsia destination`)
      }
    })
  }
  validateUniqueStrings(port.requiredBackendContracts, `${label}: opsiaPort.requiredBackendContracts`, errors, { minimum: 1 })
  if (Array.isArray(port.requiredBackendContracts)) {
    port.requiredBackendContracts.forEach((contract, index) => {
      if (!BACKEND_CONTRACT.test(contract ?? '')) {
        errors.push(`${label}: opsiaPort.requiredBackendContracts[${index}]: is not a known contract namespace`)
      }
    })
  }
  validateUniqueStrings(port.plannedTestIds, `${label}: opsiaPort.plannedTestIds`, errors, { pattern: PLANNED_TEST_ID, minimum: 1 })
  if (knownPlannedTestIds && Array.isArray(port.plannedTestIds)) {
    port.plannedTestIds.forEach((testId, index) => {
      if (!knownPlannedTestIds.has(testId)) {
        errors.push(`${label}: opsiaPort.plannedTestIds[${index}]: is not declared by classification input`)
      }
    })
  }
  if (!IMPLEMENTATION_STATES.has(port.state)) {
    errors.push(`${label}: opsiaPort.state must be in_progress or blocked`)
  }
  if (typeof port.rationale !== 'string' || !port.rationale.trim()) {
    errors.push(`${label}: opsiaPort.rationale is required`)
  }
  if (port.state === 'blocked') {
    if (typeof port.blockedReason !== 'string' || !port.blockedReason.trim()) {
      errors.push(`${label}: blocked opsiaPort requires blockedReason`)
    }
  } else if (port.blockedReason !== null) {
    errors.push(`${label}: in_progress opsiaPort requires blockedReason null`)
  }
}

function validateClassifiedInteraction(interaction, label, errors, {
  knownLegacyContractIds,
  seenLegacyContractIds,
  knownOpsiaDestinations,
  knownPlannedTestIds,
}) {
  if (!interaction || typeof interaction !== 'object' || Array.isArray(interaction)) {
    errors.push(`${label}: interaction must be an object`)
    return
  }
  if (!SOURCE_KEY.test(interaction.sourceKey ?? '')) errors.push(`${label}: immutable sourceKey is required`)
  if (typeof interaction.symbol !== 'string' || !interaction.symbol.trim()) errors.push(`${label}: source symbol is required`)
  if (typeof interaction.interaction !== 'string' || !interaction.interaction.trim()) errors.push(`${label}: semantic interaction is required`)
  if (!Array.isArray(interaction.legacyContractIds) || interaction.legacyContractIds.some((id) => !/^reference\.feature\.\d{3}$/.test(id))) {
    errors.push(`${label}: legacyContractIds must contain only reference feature aliases`)
  } else {
    for (const legacyContractId of interaction.legacyContractIds) {
      if (seenLegacyContractIds.has(legacyContractId)) {
        errors.push(`${label}: legacyContractId is duplicated: ${legacyContractId}`)
      } else {
        seenLegacyContractIds.add(legacyContractId)
      }
      if (knownLegacyContractIds && !knownLegacyContractIds.has(legacyContractId)) {
        errors.push(`${label}: legacyContractId is unknown: ${legacyContractId}`)
      }
    }
  }
  validateRealtime(interaction, label, errors)
  validateMotion(interaction, label, errors)
  validateOpsiaPort(interaction.opsiaPort, label, errors, { knownOpsiaDestinations, knownPlannedTestIds })
}

function validateRealtime(interaction, label, errors) {
  if (!TRANSPORTS.has(interaction.transport)) {
    errors.push(`${label}: transport is not supported`)
    return
  }
  if (interaction.transport === 'none') {
    if (interaction.realtime !== null) errors.push(`${label}: none transport requires realtime null`)
    return
  }
  if (!interaction.realtime || typeof interaction.realtime !== 'object') {
    errors.push(`${label}: ${interaction.transport} transport requires realtime policy`)
    return
  }
  for (const key of ['resume', 'backpressure', 'merge']) {
    if (typeof interaction.realtime[key] !== 'string' || !interaction.realtime[key].trim()) {
      errors.push(`${label}: realtime.${key} is required`)
    }
  }
}

function validateMotion(interaction, label, errors) {
  if (interaction.motion === null) return
  if (!interaction.motion || typeof interaction.motion !== 'object') {
    errors.push(`${label}: motion must be null or an object`)
    return
  }
  if (typeof interaction.motion.reducedMotion !== 'string' || !interaction.motion.reducedMotion.trim()) {
    errors.push(`${label}: motion.reducedMotion is required`)
  }
  if (!Array.isArray(interaction.motion.evidence) || interaction.motion.evidence.length === 0) {
    errors.push(`${label}: motion.evidence requires at least one item`)
    return
  }
  interaction.motion.evidence.forEach((locator, index) => {
    if (typeof locator !== 'string' || !locator.trim()) {
      errors.push(`${label}: motion.evidence[${index}] must be a non-empty locator`)
    }
  })
}

function immutableDeltaEvidence(ledger) {
  return {
    schemaVersion: ledger?.schemaVersion,
    sourceProvenance: ledger?.sourceProvenance,
    baseRevision: ledger?.baseRevision,
    targetRevision: ledger?.targetRevision,
    scope: ledger?.scope,
    fileCount: ledger?.fileCount,
    statusCounts: ledger?.statusCounts,
    files: ledger?.files?.map(({ status, previousPath, path: filePath, base, target }) => ({
      status,
      previousPath,
      path: filePath,
      base,
      target,
    })),
  }
}

function assertImmutableDeltaEvidenceMatches(current, generated, output) {
  if (JSON.stringify(immutableDeltaEvidence(current)) !== JSON.stringify(immutableDeltaEvidence(generated))) {
    throw new Error(`source UI delta ledger does not match immutable git evidence: ${output}`)
  }
}

function classificationEvidence(ledger) {
  return {
    schemaVersion: ledger?.schemaVersion,
    pendingCount: ledger?.pendingCount,
    files: ledger?.files?.map(({ path: filePath, classification, interactions }) => ({
      path: filePath,
      classification,
      interactions,
    })),
  }
}

function assertClassificationEvidenceMatches(current, generated, output) {
  if (JSON.stringify(classificationEvidence(current)) !== JSON.stringify(classificationEvidence(generated))) {
    throw new Error(`source UI delta ledger does not match generated classification input: ${output}`)
  }
}

export function validateClassificationInput(input, { sourceProvenance, targetRevision } = {}) {
  const errors = []
  if (!input || typeof input !== 'object' || Array.isArray(input)) return ['classification input must be an object']
  if (input.schemaVersion !== 1) errors.push('classification input schemaVersion must equal 1')
  if (input.sourceProvenance !== PROVENANCE_REFERENCE) {
    errors.push(`classification input sourceProvenance must equal ${PROVENANCE_REFERENCE}`)
  }
  if (!REVISION.test(input.targetRevision ?? '')) {
    errors.push('classification input targetRevision must be a 40-character lowercase hexadecimal revision')
  }
  if (sourceProvenance && input.sourceProvenance !== sourceProvenance) {
    errors.push('classification input sourceProvenance must match the generated ledger')
  }
  if (targetRevision && input.targetRevision !== targetRevision) {
    errors.push('classification input targetRevision must match the generated ledger')
  }
  if (!input.testPlans || typeof input.testPlans !== 'object' || Array.isArray(input.testPlans)) {
    errors.push('classification input testPlans must be an object')
  }
  const knownPlannedTestIds = new Set(Object.keys(input.testPlans ?? {}))
  for (const [testId, testPlan] of Object.entries(input.testPlans ?? {})) {
    if (!PLANNED_TEST_ID.test(testId)) {
      errors.push(`classification input test plan ${testId}: id is invalid`)
    }
    if (!testPlan || typeof testPlan !== 'object' || Array.isArray(testPlan)) {
      errors.push(`classification input test plan ${testId}: must be an object`)
      continue
    }
    const expectedKeys = new Set(['destination', 'rationale'])
    for (const key of Object.keys(testPlan)) {
      if (!expectedKeys.has(key)) errors.push(`classification input test plan ${testId}: ${key} is not supported`)
    }
    validatePath(testPlan.destination, `classification input test plan ${testId}: destination`, errors)
    if (!/^frontend\/.*\.test\.[tj]sx?$|^tests\/.*\.py$/.test(testPlan.destination ?? '')) {
      errors.push(`classification input test plan ${testId}: destination must be a current test path`)
    }
    if (typeof testPlan.rationale !== 'string' || !testPlan.rationale.trim()) {
      errors.push(`classification input test plan ${testId}: rationale is required`)
    }
  }
  if (!input.classifications || typeof input.classifications !== 'object' || Array.isArray(input.classifications)) {
    return [...errors, 'classification input classifications must be an object']
  }
  for (const [filePath, classification] of Object.entries(input.classifications)) {
    validatePath(filePath, `classification input ${filePath}`, errors)
    if (!classification || typeof classification !== 'object' || Array.isArray(classification)) {
      errors.push(`classification input ${filePath}: must be an object`)
      continue
    }
    if (classification.classification !== 'classified') {
      errors.push(`classification input ${filePath}: classification must equal classified`)
    }
    if (!Array.isArray(classification.interactions) || classification.interactions.length === 0) {
      errors.push(`classification input ${filePath}: interactions are required`)
      continue
    }
    classification.interactions.forEach((interaction, index) => {
      validateOpsiaPort(interaction?.opsiaPort, `classification input ${filePath}: interactions[${index}]`, errors, {
        knownPlannedTestIds,
      })
    })
  }
  return errors
}

async function readClassificationInput(classificationInput, { sourceProvenance, targetRevision }) {
  if (!classificationInput) return null
  let parsed
  try {
    parsed = JSON.parse(await readFile(classificationInput, 'utf8'))
  } catch (error) {
    throw new Error(`classification input is not valid JSON: ${classificationInput} (${error instanceof Error ? error.message : String(error)})`)
  }
  const errors = validateClassificationInput(parsed, { sourceProvenance, targetRevision })
  if (errors.length > 0) throw new Error(`classification input validation failed:\n${errors.join('\n')}`)
  return parsed
}

export function validateDeltaLedger(ledger, {
  knownLegacyContractIds = null,
  knownOpsiaDestinations = null,
  knownPlannedTestIds = null,
} = {}) {
  const errors = []
  if (!ledger || typeof ledger !== 'object') return ['ledger must be an object']
  if (ledger.schemaVersion !== DELTA_LEDGER_SCHEMA_VERSION) errors.push(`schemaVersion must equal ${DELTA_LEDGER_SCHEMA_VERSION}`)
  if (!REVISION.test(ledger.baseRevision ?? '')) errors.push('baseRevision must be a 40-character lowercase hexadecimal revision')
  if (!REVISION.test(ledger.targetRevision ?? '')) errors.push('targetRevision must be a 40-character lowercase hexadecimal revision')
  if (ledger.sourceProvenance !== PROVENANCE_REFERENCE) errors.push(`sourceProvenance must equal ${PROVENANCE_REFERENCE}`)
  if (!Array.isArray(ledger.scope) || ledger.scope.length === 0) errors.push('scope must be a non-empty array')
  if (!Array.isArray(ledger.files)) return [...errors, 'files must be an array']
  if (ledger.fileCount !== ledger.files.length) errors.push('fileCount must equal files.length')
  const pendingCount = ledger.files.filter((row) => row.classification === 'pending').length
  if (ledger.pendingCount !== pendingCount) errors.push('pendingCount must equal pending files')
  if (!ledger.statusCounts || typeof ledger.statusCounts !== 'object' || Array.isArray(ledger.statusCounts)) {
    errors.push('statusCounts must be an object')
  } else {
    for (const status of CHANGE_STATUSES) {
      const actual = ledger.files.filter((row) => row.status === status).length
      if (ledger.statusCounts[status] !== actual) {
        errors.push(`statusCounts.${status} must equal ${actual}`)
      }
    }
    for (const status of Object.keys(ledger.statusCounts)) {
      if (!CHANGE_STATUSES.has(status)) errors.push(`statusCounts.${status} is not supported`)
    }
  }

  const seenPaths = new Set()
  const seenSourceKeys = new Set()
  const seenLegacyContractIds = new Set()
  for (const row of ledger.files) {
    const label = String(row?.path ?? '<unknown>')
    validatePath(row?.path, label, errors)
    const identity = `${row?.status}:${row?.previousPath ?? ''}:${row?.path ?? ''}`
    if (seenPaths.has(identity)) errors.push(`${label}: change row is duplicated`)
    seenPaths.add(identity)
    if (!CHANGE_STATUSES.has(row?.status)) {
      errors.push(`${label}: status is not supported`)
      continue
    }
    const needsBase = row.status === 'M' || row.status === 'D' || row.status === 'R'
    const needsTarget = row.status === 'A' || row.status === 'M' || row.status === 'R'
    if (needsBase) validateEvidence(row.base, `${label}: base`, errors)
    else if (row.base !== null) errors.push(`${label}: ${row.status} row requires base null`)
    if (needsTarget) validateEvidence(row.target, `${label}: target`, errors)
    else if (row.target !== null) errors.push(`${label}: ${row.status} row requires target null`)
    if (row.status === 'R') validatePath(row.previousPath, `${label}: previous`, errors)
    else if (row.previousPath !== null) errors.push(`${label}: ${row.status} row requires previousPath null`)
    if (!CLASSIFICATIONS.has(row.classification)) {
      errors.push(`${label}: classification is not supported`)
      continue
    }
    if (FILE_LEVEL_INTERACTION_EVIDENCE_KEYS.some((key) => Object.hasOwn(row, key))) {
      errors.push(`${label}: file-level interaction evidence must use interactions[]`)
    }
    if (row.classification === 'pending') {
      if (!Array.isArray(row.interactions) || row.interactions.length !== 0) {
        errors.push(`${label}: pending row must not claim interaction evidence`)
      }
      continue
    }
    if (!Array.isArray(row.interactions) || row.interactions.length === 0) {
      errors.push(`${label}: classified row requires non-empty interactions[]`)
      continue
    }
    row.interactions.forEach((interaction, index) => {
      const interactionLabel = `${label}: interactions[${index}]`
      validateClassifiedInteraction(interaction, interactionLabel, errors, {
        knownLegacyContractIds,
        seenLegacyContractIds,
        knownOpsiaDestinations,
        knownPlannedTestIds,
      })
      if (SOURCE_KEY.test(interaction?.sourceKey ?? '')) {
        if (seenSourceKeys.has(interaction.sourceKey)) errors.push(`${interactionLabel}: sourceKey is duplicated`)
        else seenSourceKeys.add(interaction.sourceKey)
      }
    })
  }
  return errors
}

export function assertDeltaLedgerClassified(ledger, validationContext = undefined) {
  const errors = validateDeltaLedger(ledger, validationContext)
  if (errors.length > 0) throw new Error(`source delta ledger validation failed:\n${errors.join('\n')}`)
  const pending = ledger.files.filter((row) => row.classification === 'pending')
  if (pending.length > 0) {
    throw new Error(`source delta release gate blocked: ${pending.length}개 pending source delta 항목이 남아 있습니다`)
  }
}

export function declaredInventoryRevision(markdown) {
  const match = String(markdown).match(/\bcommit\s+`([0-9a-f]{40})`/)
  if (!match) throw new Error('inventory declared source revision is missing')
  return match[1]
}

export function assertInventoryRevisionMatchesTarget(markdown, targetRevision) {
  const declared = declaredInventoryRevision(markdown)
  if (declared !== targetRevision) {
    throw new Error(`inventory source revision ${declared} does not match target ${targetRevision}; latest-source parity is not rebased`)
  }
  return declared
}

function runGit(repository, args, input = null) {
  return new Promise((resolve, reject) => {
    const child = spawn('git', ['-C', repository, ...args], { stdio: ['pipe', 'pipe', 'pipe'] })
    const stdout = []
    const stderr = []
    child.stdout.on('data', (chunk) => stdout.push(chunk))
    child.stderr.on('data', (chunk) => stderr.push(chunk))
    child.on('error', reject)
    child.on('close', (code) => {
      if (code === 0) {
        resolve(Buffer.concat(stdout))
        return
      }
      reject(new Error(`git ${args.join(' ')} failed (${code}): ${Buffer.concat(stderr).toString('utf8').trim()}`))
    })
    if (input) child.stdin.end(input)
    else child.stdin.end()
  })
}

async function readKnownOpsiaDestinations() {
  const output = await runGit(REPOSITORY_ROOT, ['ls-files', '-z', '--', 'desktop', 'frontend', 'src', 'tests'])
  return new Set(output.toString('utf8').split('\0').filter(Boolean).map(normalizedPath))
}

async function classificationValidationContext(classificationInput) {
  if (!classificationInput) return {}
  return {
    knownOpsiaDestinations: await readKnownOpsiaDestinations(),
    knownPlannedTestIds: new Set(Object.keys(classificationInput.testPlans)),
  }
}

function parseNameStatus(buffer) {
  const values = buffer.toString('utf8').split('\0').filter(Boolean)
  const changes = []
  for (let index = 0; index < values.length;) {
    const rawStatus = values[index++]
    const status = rawStatus[0]
    if (status === 'R') {
      const previousPath = values[index++]
      const filePath = values[index++]
      changes.push({ status, previousPath, path: filePath })
      continue
    }
    if (!CHANGE_STATUSES.has(status)) throw new Error(`unsupported source change status from git: ${rawStatus}`)
    changes.push({ status, path: values[index++] })
  }
  return changes
}

function parseTree(buffer) {
  const files = new Map()
  for (const value of buffer.toString('utf8').split('\0').filter(Boolean)) {
    const separator = value.indexOf('\t')
    const metadata = value.slice(0, separator).split(' ')
    const filePath = value.slice(separator + 1)
    if (metadata[1] === 'blob') files.set(filePath, metadata[2])
  }
  return files
}

async function readBlobEvidence(repository, blobIds) {
  const unique = [...new Set(blobIds)].sort()
  if (unique.length === 0) return new Map()
  const output = await runGit(repository, ['cat-file', '--batch'], Buffer.from(`${unique.join('\n')}\n`))
  const evidence = new Map()
  let offset = 0
  while (offset < output.length) {
    const newline = output.indexOf(0x0a, offset)
    if (newline < 0) throw new Error('git cat-file batch response has an incomplete header')
    const header = output.subarray(offset, newline).toString('utf8').split(' ')
    const [blobId, type, rawSize] = header
    const size = Number(rawSize)
    offset = newline + 1
    if (type !== 'blob' || !Number.isInteger(size) || size < 0) throw new Error(`git cat-file batch response is not a blob: ${header.join(' ')}`)
    const content = output.subarray(offset, offset + size)
    if (content.length !== size) throw new Error(`git cat-file batch response truncated blob ${blobId}`)
    evidence.set(blobId, { blobId, sha256: createHash('sha256').update(content).digest('hex') })
    offset += size + 1
  }
  return evidence
}

export async function collectGitDelta({
  repository = DEFAULT_REPOSITORY,
  baseRevision = DEFAULT_BASE_REVISION,
  targetRevision = DEFAULT_TARGET_REVISION,
  scope = DEFAULT_SCOPE,
} = {}) {
  const [statusOutput, baseTreeOutput, targetTreeOutput] = await Promise.all([
    runGit(repository, ['diff', '--name-status', '-z', '-M', baseRevision, targetRevision, '--', ...scope]),
    runGit(repository, ['ls-tree', '-r', '-z', baseRevision, '--', ...scope]),
    runGit(repository, ['ls-tree', '-r', '-z', targetRevision, '--', ...scope]),
  ])
  const changes = parseNameStatus(statusOutput)
  const baseBlobs = parseTree(baseTreeOutput)
  const targetBlobs = parseTree(targetTreeOutput)
  const blobEvidence = await readBlobEvidence(repository, [...baseBlobs.values(), ...targetBlobs.values()])
  const baseFiles = new Map([...baseBlobs].map(([filePath, blobId]) => [filePath, blobEvidence.get(blobId)]))
  const targetFiles = new Map([...targetBlobs].map(([filePath, blobId]) => [filePath, blobEvidence.get(blobId)]))
  return { changes, baseFiles, targetFiles }
}

export async function createDeltaLedger(options = {}) {
  const resolved = resolveDeltaLedgerOptions(options)
  const [source, classificationInput] = await Promise.all([
    collectGitDelta(resolved),
    readClassificationInput(resolved.classificationInput, resolved),
  ])
  const ledger = buildDeltaLedger({
    ...resolved,
    ...source,
    classifications: classificationInput?.classifications ?? null,
  })
  const validationErrors = validateDeltaLedger(ledger, await classificationValidationContext(classificationInput))
  if (validationErrors.length > 0) throw new Error(`source delta ledger validation failed:\n${validationErrors.join('\n')}`)
  return ledger
}

function parseArguments(argv) {
  const values = {
    repository: DEFAULT_REPOSITORY,
    baseRevision: DEFAULT_BASE_REVISION,
    targetRevision: DEFAULT_TARGET_REVISION,
    inventory: DEFAULT_INVENTORY,
    output: DEFAULT_OUTPUT,
    featureLedger: DEFAULT_FEATURE_LEDGER,
    classificationInput: DEFAULT_CLASSIFICATION_INPUT,
    check: false,
    requireClassified: false,
    requireRebased: false,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]
    if (flag === '--check') {
      values.check = true
      continue
    }
    if (flag === '--require-classified') {
      values.requireClassified = true
      continue
    }
    if (flag === '--require-rebased') {
      values.requireRebased = true
      continue
    }
    if (!['--repository', '--base', '--target', '--inventory', '--output', '--feature-ledger', '--classification-input'].includes(flag) || !argv[index + 1]) {
      throw new Error(`unsupported argument: ${flag}`)
    }
    const value = argv[index + 1]
    if (flag === '--base') values.baseRevision = value
    else if (flag === '--target') values.targetRevision = value
    else if (flag === '--feature-ledger') values.featureLedger = path.resolve(value)
    else if (flag === '--classification-input') values.classificationInput = path.resolve(value)
    else values[flag.slice(2)] = path.resolve(value)
    index += 1
  }
  return values
}

function knownLegacyContractIdsFromFeatureLedger(ledger) {
  if (!ledger || typeof ledger !== 'object' || !Array.isArray(ledger.features)) {
    throw new Error('feature ledger must contain a features array for source interaction alias validation')
  }
  const knownLegacyContractIds = new Set()
  for (const feature of ledger.features) {
    const contractId = feature?.contractId
    if (!/^reference\.feature\.\d{3}$/.test(contractId ?? '')) {
      throw new Error('feature ledger contains an invalid contractId for source interaction alias validation')
    }
    if (knownLegacyContractIds.has(contractId)) {
      throw new Error(`feature ledger contains a duplicate contractId for source interaction alias validation: ${contractId}`)
    }
    knownLegacyContractIds.add(contractId)
  }
  return knownLegacyContractIds
}

async function readKnownLegacyContractIds(featureLedger) {
  const parsed = JSON.parse(await readFile(featureLedger, 'utf8'))
  return knownLegacyContractIdsFromFeatureLedger(parsed)
}

export async function writeDeltaLedger({
  repository,
  baseRevision,
  targetRevision,
  inventory,
  output,
  featureLedger = DEFAULT_FEATURE_LEDGER,
  classificationInput = null,
  check = false,
  requireClassified = false,
  requireRebased = false,
}) {
  const generated = await createDeltaLedger({ repository, baseRevision, targetRevision, classificationInput })
  let ledger = generated
  let validationContext
  if (check) {
    const current = await readFile(output, 'utf8').catch(() => null)
    if (current === null) throw new Error(`source UI delta ledger does not match immutable git evidence: ${output}`)
    let currentLedger
    try {
      currentLedger = JSON.parse(current)
    } catch {
      throw new Error(`source UI delta ledger is not valid JSON: ${output}`)
    }
    const parsedClassificationInput = await readClassificationInput(classificationInput, {
      sourceProvenance: PROVENANCE_REFERENCE,
      targetRevision,
    })
    validationContext = {
      knownLegacyContractIds: await readKnownLegacyContractIds(featureLedger),
      ...(await classificationValidationContext(parsedClassificationInput)),
    }
    const validationErrors = validateDeltaLedger(currentLedger, validationContext)
    if (validationErrors.length > 0) {
      throw new Error(`source delta ledger validation failed:\n${validationErrors.join('\n')}`)
    }
    assertImmutableDeltaEvidenceMatches(currentLedger, generated, output)
    if (classificationInput) assertClassificationEvidenceMatches(currentLedger, generated, output)
    ledger = currentLedger
  } else {
    const serialized = `${JSON.stringify(generated, null, 2)}\n`
    await mkdir(path.dirname(output), { recursive: true })
    await writeFile(output, serialized, 'utf8')
  }
  if (requireRebased) {
    const markdown = await readFile(inventory, 'utf8')
    assertInventoryRevisionMatchesTarget(markdown, targetRevision)
  }
  if (requireClassified) assertDeltaLedgerClassified(ledger, validationContext)
  return ledger
}

async function main() {
  const options = parseArguments(process.argv.slice(2))
  const ledger = await writeDeltaLedger(options)
  process.stdout.write(`source UI delta ledger complete: ${ledger.fileCount} files, ${ledger.pendingCount} pending\n`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  })
}
