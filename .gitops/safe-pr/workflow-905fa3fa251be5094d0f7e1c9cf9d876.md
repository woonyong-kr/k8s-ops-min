# Apply sandbox manifest

deployment/session-gateway: ghcr.io/jungle-303-04/demo-game/session-gateway:276977d3628e8cc5c7ce8c362add3b4111c7883e → ghcr.io/jungle-303-04/demo-game/session-gateway:stable

## GitOps Basis

- approval_ref: `approval-381d056577df4dcc1995bb4b59f42462`
- policy_decision_ref: `policy-decision:approval-381d056577df4dcc1995bb4b59f42462:safe_pr`
- diff_status: `intended_change`
- diff_basis: `managed-field-3way`
- artifact_digest: `sha256:8f60b2e836b46e64524bd0e248c66889a0bfbe02fc37930d3484c0285de2ebb1`
- rollback_patch: `.gitops/rollback/workflow-905fa3fa251be5094d0f7e1c9cf9d876/deployment-session-gateway-base.yaml`


- manifest_path: `deploy/k8s/base`
- pr_kind: `safe_pr_patch`
- workflow_run_id: `workflow-905fa3fa251be5094d0f7e1c9cf9d876`
- environment: `development`

## Evidence

- commit_sha: ``
- patch_sha256: `4bbbfbeb131fe34955d3ae69e7bd8c630311438702fa1f3d650b742a2bb50b28`

## Approval

- approval_ref: `approval-381d056577df4dcc1995bb4b59f42462`
- policy_decision_ref: `policy-decision:approval-381d056577df4dcc1995bb4b59f42462:safe_pr`

## Files

- `deploy/k8s/base`: rendered Kubernetes manifest
- `.gitops/rollback/workflow-905fa3fa251be5094d0f7e1c9cf9d876/deployment-session-gateway-base.yaml`: rollback manifest generated from live/previous values
