# Apply sandbox manifest

deployment/api-server: apply rendered manifest

## GitOps Basis

- approval_ref: `approval-3f457c61e086eecc62e9ed3f386f9c6b`
- policy_decision_ref: `policy-decision:approval-3f457c61e086eecc62e9ed3f386f9c6b:safe_pr`
- diff_status: `intended_change`
- diff_basis: `managed-field-3way`
- artifact_digest: `sha256:b6b20ae1523daa5616cb3ae5d2d5bce5de04b73b37efc9fa245fe1d3be2a6252`
- rollback_patch: `.gitops/rollback/workflow-94e3a6f78e5ccb60a8b20dc0be8b5601/deployment-api-server-base.yaml`


- manifest_path: `deploy/k8s/base`
- pr_kind: `safe_pr_patch`
- workflow_run_id: `workflow-94e3a6f78e5ccb60a8b20dc0be8b5601`
- environment: `development`

## Evidence

- commit_sha: ``
- patch_sha256: `dcdd808aa5e26943970146743ce0850188280aae8c289f97d8290d4ae2ba604a`

## Approval

- approval_ref: `approval-3f457c61e086eecc62e9ed3f386f9c6b`
- policy_decision_ref: `policy-decision:approval-3f457c61e086eecc62e9ed3f386f9c6b:safe_pr`

## Files

- `deploy/k8s/base`: rendered Kubernetes manifest
- `.gitops/rollback/workflow-94e3a6f78e5ccb60a8b20dc0be8b5601/deployment-api-server-base.yaml`: rollback manifest generated from live/previous values
