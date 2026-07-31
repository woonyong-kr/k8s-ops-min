# Apply sandbox manifest

deployment/game-room-0: unknown → ghcr.io/jungle-303-04/demo-game/game-server:stable

## GitOps Basis

- approval_ref: `approval-918cf6e0a99012551585e735584944fb`
- policy_decision_ref: `policy-decision:approval-918cf6e0a99012551585e735584944fb:safe_pr`
- diff_status: `intended_change`
- diff_basis: `managed-field-3way`
- artifact_digest: `sha256:81e5842c998ade4883d71abefa13d3e18aaf269e02520e3c3ee73d0261dd2f6f`
- rollback_patch: `.gitops/rollback/workflow-a1c8e846e590c3e62a8ff10d27883eca/deployment-game-room-0-base.yaml`


- manifest_path: `deploy/k8s/base`
- pr_kind: `safe_pr_patch`
- workflow_run_id: `workflow-a1c8e846e590c3e62a8ff10d27883eca`
- environment: `development`

## Evidence

- commit_sha: ``
- patch_sha256: `28ffb6e12d05450d19407e9c33d6aa31f8b227c1e07d81c0e224d503bfdd2396`

## Approval

- approval_ref: `approval-918cf6e0a99012551585e735584944fb`
- policy_decision_ref: `policy-decision:approval-918cf6e0a99012551585e735584944fb:safe_pr`

## Files

- `deploy/k8s/base`: rendered Kubernetes manifest
- `.gitops/rollback/workflow-a1c8e846e590c3e62a8ff10d27883eca/deployment-game-room-0-base.yaml`: rollback manifest generated from live/previous values
