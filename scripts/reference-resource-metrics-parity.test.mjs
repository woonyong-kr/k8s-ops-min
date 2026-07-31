import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const ROOT = new URL("../", import.meta.url);

async function readJson(path) {
  return JSON.parse(await readFile(new URL(path, ROOT), "utf8"));
}

const FEATURE_IDS = Array.from(
  { length: 40 },
  (_, index) => `reference.feature.${String(index + 124).padStart(3, "0")}`,
);

const RESOURCE_FILES_IMPLEMENTED = new Set([
  "reference.feature.136",
  "reference.feature.137",
  "reference.feature.138",
  "reference.feature.139",
  "reference.feature.140",
]);

const AGENT_ARCHITECTURE_EXCLUDED = new Set([
  "reference.feature.141",
  "reference.feature.142",
]);

const PROMETHEUS_RESOURCE_IMPLEMENTED = new Set([
  "reference.feature.149",
  "reference.feature.150",
  "reference.feature.151",
  "reference.feature.152",
]);

test("resource, metrics, logs, and service access rows own immutable source evidence", async () => {
  const [ports, aliases, classifications, identities, ledger] = await Promise.all([
    readJson("docs/migration/reference-feature-port-map.json"),
    readJson("docs/migration/reference-feature-source-aliases.json"),
    readJson("docs/migration/reference-ui-delta-classifications.json"),
    readJson("docs/migration/reference-feature-source-identities.json"),
    readJson("docs/migration/reference-feature-ledger.json"),
  ]);
  const sourceOwners = new Map();
  for (const [path, classification] of Object.entries(classifications.classifications)) {
    for (const interaction of classification.interactions ?? []) {
      for (const contractId of interaction.legacyContractIds ?? []) {
        assert.equal(sourceOwners.has(contractId), false, `${contractId} source owner is duplicated`);
        sourceOwners.set(contractId, { path, sourceKey: interaction.sourceKey });
      }
    }
  }
  for (const identity of identities.identities) {
    if (!identity.legacyContractId) continue;
    assert.equal(
      sourceOwners.has(identity.legacyContractId),
      false,
      `${identity.legacyContractId} source owner is duplicated`,
    );
    sourceOwners.set(identity.legacyContractId, {
      path: identity.evidence[0]?.path,
      sourceKey: identity.sourceKey,
    });
  }
  const ledgerById = new Map(
    ledger.features.map((feature) => [feature.contractId, feature]),
  );

  for (const contractId of FEATURE_IDS) {
    const port = ports.features[contractId];
    const sourceKey = aliases.aliases[contractId];
    const feature = ledgerById.get(contractId);
    assert.ok(port, `${contractId} requires an explicit port decision`);
    assert.match(sourceKey, /^upstream-ui:[a-z0-9][a-z0-9:-]+:v1$/, contractId);
    assert.equal(sourceOwners.get(contractId)?.sourceKey, sourceKey, contractId);
    assert.equal(feature?.sourceKey, sourceKey, contractId);
    assert.equal(feature?.deliveryStatus, port.deliveryStatus, contractId);
    assert.ok(port.coverage?.backend, `${contractId} requires backend coverage`);
    assert.ok(port.coverage?.frontend, `${contractId} requires frontend coverage`);

    if (port.deliveryStatus === "implemented") {
      assert.equal(port.deliveryStatus, "implemented", contractId);
      assert.ok(
        ["implemented", "not_required"].includes(port.coverage.backend.state),
        contractId,
      );
      assert.equal(port.coverage.frontend.state, "implemented", contractId);
    } else if (AGENT_ARCHITECTURE_EXCLUDED.has(contractId)) {
      assert.equal(port.deliveryStatus, "not_applicable", contractId);
      assert.equal(port.coverage.backend.state, "not_required", contractId);
      assert.equal(port.coverage.frontend.state, "not_required", contractId);
      assert.match(port.coverage.backend.reason, /outbound Agent/u, contractId);
    } else {
      assert.equal(port.deliveryStatus, "in_progress", contractId);
      assert.ok(
        Object.values(port.coverage).some((item) =>
          ["in_progress", "blocked"].includes(item?.state)
        ),
        `${contractId} requires an explicit incomplete boundary`,
      );
    }
  }
});

test("resource files and native port authority remain explicit", async () => {
  const ports = await readJson("docs/migration/reference-feature-port-map.json");
  for (const contractId of RESOURCE_FILES_IMPLEMENTED) {
    const port = ports.features[contractId];
    assert.equal(port.deliveryStatus, "implemented", contractId);
    assert.equal(port.coverage.backend.state, "implemented", contractId);
    assert.equal(port.coverage.frontend.state, "implemented", contractId);
    assert.match(port.backendContract, /resource_files/u, contractId);
    assert.match(port.frontendContract, /resource-files/u, contractId);
  }
  assert.equal(
    ports.features["reference.feature.159"].coverage.desktop.state,
    "implemented",
  );
  for (const contractId of [
    "reference.feature.160",
    "reference.feature.162",
    "reference.feature.163",
  ]) {
    assert.equal(ports.features[contractId].deliveryStatus, "in_progress", contractId);
    assert.equal(ports.features[contractId].coverage.desktop.state, "blocked", contractId);
  }

  const resourceAudit = ports.features["reference.feature.129"];
  assert.equal(
    resourceAudit.coverage.backend.test,
    "tests/test_checks_router.py#test_checks_overview_filters_exact_resource_identity_and_rejects_ambiguous_scope",
  );
  assert.equal(resourceAudit.deliveryStatus, "implemented");
  assert.equal(resourceAudit.coverage.frontend.state, "implemented");
  assert.equal(resourceAudit.verification.includes("tests/test_audit.py"), false);
});

test("Prometheus status, connection, resource categories, and HPA ranges reuse typed runtime ports", async () => {
  const ports = await readJson("docs/migration/reference-feature-port-map.json");
  for (const contractId of PROMETHEUS_RESOURCE_IMPLEMENTED) {
    const port = ports.features[contractId];
    assert.equal(port.deliveryStatus, "implemented", contractId);
    assert.equal(port.coverage.backend.state, "implemented", contractId);
    assert.equal(port.coverage.frontend.state, "implemented", contractId);
    assert.match(port.backendContract, /scoped_metrics/, contractId);
  }
});

test("pod and workload stream parity uses bounded authenticated realtime contracts", async () => {
  const ports = await readJson("docs/migration/reference-feature-port-map.json");
  for (const contractId of [
    "reference.feature.156",
    "reference.feature.157",
    "reference.feature.158",
    "reference.feature.159",
    "reference.feature.155",
  ]) {
    assert.equal(ports.features[contractId].coverage.realtime.state, "implemented", contractId);
  }
});
