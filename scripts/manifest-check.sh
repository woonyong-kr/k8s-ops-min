#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

MANAGEMENT_MANIFEST="${TMP_DIR}/management.yaml"
TARGET_MANIFEST="${TMP_DIR}/target.yaml"
MANAGEMENT_OBJECTS="${TMP_DIR}/management.objects"
TARGET_OBJECTS="${TMP_DIR}/target.objects"

kubectl kustomize "${ROOT_DIR}/deploy/management" > "${MANAGEMENT_MANIFEST}"
sed \
  -e 's#__MANAGEMENT_BASE_URL__#http://api-gateway.management:8000#g' \
  -e 's#__REALTIME_GATEWAY_URL__#ws://realtime-gateway.management:8000#g' \
  "${ROOT_DIR}/deploy/target/target.yaml" > "${TARGET_MANIFEST}"
printf '\n---\n' >> "${TARGET_MANIFEST}"
cat "${ROOT_DIR}/deploy/target/minio.yaml" >> "${TARGET_MANIFEST}"

python3 - "${MANAGEMENT_MANIFEST}" "${MANAGEMENT_OBJECTS}" <<'PY'
from pathlib import Path
import sys

manifest = Path(sys.argv[1])
out = Path(sys.argv[2])
objects = []

for index, raw_doc in enumerate(manifest.read_text(encoding="utf-8").split("\n---"), start=1):
    lines = [line.rstrip() for line in raw_doc.splitlines() if line.strip()]
    if not lines:
        continue

    values = {}
    in_metadata = False
    for line in lines:
        if not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
            in_metadata = key.strip() == "metadata"
            continue
        if in_metadata and line.startswith("  name:"):
            values["metadata.name"] = line.split(":", 1)[1].strip()

    missing = [
        field
        for field in ("apiVersion", "kind", "metadata.name")
        if not values.get(field)
    ]
    if missing:
        raise SystemExit(f"{manifest}: document {index} missing {', '.join(missing)}")
    objects.append(f"{values['kind'].lower()}/{values['metadata.name']}")

if not objects:
    raise SystemExit(f"{manifest}: no Kubernetes objects found")

out.write_text("\n".join(objects) + "\n", encoding="utf-8")
PY

python3 - "${TARGET_MANIFEST}" "${TARGET_OBJECTS}" <<'PY'
from pathlib import Path
import sys

manifest = Path(sys.argv[1])
out = Path(sys.argv[2])
objects = []

for index, raw_doc in enumerate(manifest.read_text(encoding="utf-8").split("\n---"), start=1):
    lines = [line.rstrip() for line in raw_doc.splitlines() if line.strip()]
    if not lines:
        continue

    values = {}
    in_metadata = False
    for line in lines:
        if not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
            in_metadata = key.strip() == "metadata"
            continue
        if in_metadata and line.startswith("  name:"):
            values["metadata.name"] = line.split(":", 1)[1].strip()

    missing = [
        field
        for field in ("apiVersion", "kind", "metadata.name")
        if not values.get(field)
    ]
    if missing:
        raise SystemExit(f"{manifest}: document {index} missing {', '.join(missing)}")
    objects.append(f"{values['kind'].lower()}/{values['metadata.name']}")

if not objects:
    raise SystemExit(f"{manifest}: no Kubernetes objects found")

out.write_text("\n".join(objects) + "\n", encoding="utf-8")
PY

test -s "${MANAGEMENT_OBJECTS}"
test -s "${TARGET_OBJECTS}"

uv run python "${ROOT_DIR}/scripts/verify_dev_auth_bypass.py" rendered \
  --manifest "${MANAGEMENT_MANIFEST}"

EXPECTED_ALEMBIC_HEAD="$(
  cd "${ROOT_DIR}"
  PYTHONPATH="${ROOT_DIR}/src" uv run alembic heads | awk '{print $1}'
)"
for migration_manifest in \
  "${ROOT_DIR}/deploy/management/migration-job.yaml" \
  "${ROOT_DIR}/deploy/management/admin-bootstrap-job.yaml"
do
  configured_head="$(
    awk '
      $0 ~ /^[[:space:]]*- name: MIGRATION_EXPECTED_HEAD[[:space:]]*$/ {
        getline
        sub(/^[[:space:]]*value:[[:space:]]*"?/, "")
        sub(/"[[:space:]]*$/, "")
        print
      }
    ' "${migration_manifest}"
  )"
  if [[ "${configured_head}" != "${EXPECTED_ALEMBIC_HEAD}" ]]; then
    echo "${migration_manifest}: MIGRATION_EXPECTED_HEAD=${configured_head:-<missing>} must equal ${EXPECTED_ALEMBIC_HEAD}" >&2
    exit 1
  fi
done

echo "management manifest objects: $(wc -l < "${MANAGEMENT_OBJECTS}" | tr -d ' ')"
echo "target manifest objects: $(wc -l < "${TARGET_OBJECTS}" | tr -d ' ')"
