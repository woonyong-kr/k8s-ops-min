# Apply sandbox manifest

service/session-gateway: apply rendered manifest

## GitOps Basis

- approval_ref: `approval-edd9fa4f76eaf2efa85eba4814d9904a`
- policy_decision_ref: `policy-decision:approval-edd9fa4f76eaf2efa85eba4814d9904a:safe_pr`
- diff_status: `drift`
- diff_basis: `managed-field-3way`
- artifact_digest: `sha256:4492ba48c302227bd9867ce39b43c08450e82756500bf7889806ad2c14715ae8`
- rollback_patch: `.gitops/rollback/workflow-600ecc1a6e1e9fd84a4157ec9c9f1e90/service-session-gateway-base.yaml`


- manifest_path: `deploy/k8s/base`
- pr_kind: `safe_pr_patch`
- workflow_run_id: `workflow-600ecc1a6e1e9fd84a4157ec9c9f1e90`
- environment: `development`

## Evidence

- commit_sha: ``
- patch_sha256: `e23de9c85fa9b8ccf0a1fb5f0596a4d991a8a0f8845684f010ec7a637a816289`

## Approval

- approval_ref: `approval-edd9fa4f76eaf2efa85eba4814d9904a`
- policy_decision_ref: `policy-decision:approval-edd9fa4f76eaf2efa85eba4814d9904a:safe_pr`

## Files

- `deploy/k8s/base`: rendered Kubernetes manifest
- `.gitops/rollback/workflow-600ecc1a6e1e9fd84a4157ec9c9f1e90/service-session-gateway-base.yaml`: rollback manifest generated from live/previous values
