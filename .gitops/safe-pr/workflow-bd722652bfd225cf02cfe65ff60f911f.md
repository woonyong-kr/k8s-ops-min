# Apply sandbox manifest

deployment/api-server: apply rendered manifest

## GitOps Basis

- approval_ref: `approval-08f7d183424605843b2c7742570c1ffa`
- policy_decision_ref: `policy-decision:approval-08f7d183424605843b2c7742570c1ffa:safe_pr`
- diff_status: `drift`
- diff_basis: `managed-field-3way`
- artifact_digest: `sha256:38589d55dbfc39728d6792f388986b6ee3a767d75a54a8db15dd4902da374b21`
- rollback_patch: `.gitops/rollback/workflow-bd722652bfd225cf02cfe65ff60f911f/deployment-api-server-base.yaml`


- manifest_path: `deploy/k8s/base`
- pr_kind: `safe_pr_patch`
- workflow_run_id: `workflow-bd722652bfd225cf02cfe65ff60f911f`
- environment: `development`

## Evidence

- commit_sha: ``
- patch_sha256: `5f6a1a8f362a3552d44a919cf4fa9258cc65e862c3eec55c2a7fefba3fe6f6f8`

## Approval

- approval_ref: `approval-08f7d183424605843b2c7742570c1ffa`
- policy_decision_ref: `policy-decision:approval-08f7d183424605843b2c7742570c1ffa:safe_pr`

## Files

- `deploy/k8s/base`: rendered Kubernetes manifest
- `.gitops/rollback/workflow-bd722652bfd225cf02cfe65ff60f911f/deployment-api-server-base.yaml`: rollback manifest generated from live/previous values
