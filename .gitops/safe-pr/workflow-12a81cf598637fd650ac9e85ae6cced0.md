# Apply sandbox manifest

deployment/game-room-3: unknown → ghcr.io/jungle-303-04/demo-game/game-server:stable

## GitOps Basis

- approval_ref: `approval-3a485c20a850479d7d2dc700ca6234c1`
- policy_decision_ref: `policy-decision:approval-3a485c20a850479d7d2dc700ca6234c1:safe_pr`
- diff_status: `intended_change`
- diff_basis: `managed-field-3way`
- artifact_digest: `sha256:df52eef78a3f398547e29405bc1f2858ee7dbfe1d0cafcb3fb65f0ceaca4e8b4`
- rollback_patch: `.gitops/rollback/workflow-12a81cf598637fd650ac9e85ae6cced0/deployment-game-room-3-base.yaml`


- manifest_path: `deploy/k8s/base`
- pr_kind: `safe_pr_patch`
- workflow_run_id: `workflow-12a81cf598637fd650ac9e85ae6cced0`
- environment: `development`

## Evidence

- commit_sha: ``
- patch_sha256: `624595791a21fdbf80aa4088cedf03b55bd08fe470435a96837ad249335e8cfa`

## Approval

- approval_ref: `approval-3a485c20a850479d7d2dc700ca6234c1`
- policy_decision_ref: `policy-decision:approval-3a485c20a850479d7d2dc700ca6234c1:safe_pr`

## Files

- `deploy/k8s/base`: rendered Kubernetes manifest
- `.gitops/rollback/workflow-12a81cf598637fd650ac9e85ae6cced0/deployment-game-room-3-base.yaml`: rollback manifest generated from live/previous values
