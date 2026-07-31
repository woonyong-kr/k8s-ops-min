# Apply sandbox manifest

deployment/game-room-3: unknown → ghcr.io/jungle-303-04/demo-game/game-server:stable

## GitOps Basis

- approval_ref: `approval-343c9bb420acad61df3be70ffd12609f`
- policy_decision_ref: `policy-decision:approval-343c9bb420acad61df3be70ffd12609f:safe_pr`
- diff_status: `intended_change`
- diff_basis: `managed-field-3way`
- artifact_digest: `sha256:df52eef78a3f398547e29405bc1f2858ee7dbfe1d0cafcb3fb65f0ceaca4e8b4`
- rollback_patch: `.gitops/rollback/workflow-66b6dce2f3e031fcd4285113b95a5be6/deployment-game-room-3-base.yaml`


- manifest_path: `deploy/k8s/base`
- pr_kind: `safe_pr_patch`
- workflow_run_id: `workflow-66b6dce2f3e031fcd4285113b95a5be6`
- environment: `development`

## Evidence

- commit_sha: ``
- patch_sha256: `b5c4b58baa367818fe07effa2af5bf78adf46e905a1a65fcf2cae5bd052b0da5`

## Approval

- approval_ref: `approval-343c9bb420acad61df3be70ffd12609f`
- policy_decision_ref: `policy-decision:approval-343c9bb420acad61df3be70ffd12609f:safe_pr`

## Files

- `deploy/k8s/base`: rendered Kubernetes manifest
- `.gitops/rollback/workflow-66b6dce2f3e031fcd4285113b95a5be6/deployment-game-room-3-base.yaml`: rollback manifest generated from live/previous values
