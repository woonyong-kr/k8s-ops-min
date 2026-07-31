import { z } from "zod";

export const repositoryProbeSchema = z.strictObject({
  repo_ref: z.string().min(1),
  normalized_repo_ref: z.string().min(1),
  valid: z.boolean(),
  reachable: z.boolean(),
  default_branch: z.string().min(1).nullable(),
  private: z.boolean().nullable(),
  html_url: z.string().url().nullable(),
  warnings: z.array(z.string()),
  errors: z.array(z.string()),
});

export const repositoryBranchSchema = z.strictObject({
  name: z.string().min(1),
  protected: z.boolean(),
  default: z.boolean(),
});

export const repositoryBranchListSchema = z.strictObject({
  repo_ref: z.string().min(1),
  default_branch: z.string().min(1).nullable(),
  branches: z.array(repositoryBranchSchema),
  warnings: z.array(z.string()),
});

export const repositoryManifestCandidateSchema = z.strictObject({
  path: z.string().min(1),
  source_type: z.string(),
  display_name: z.string(),
  reason: z.string(),
});

export const repositoryManifestCandidateListSchema = z.strictObject({
  repo_ref: z.string().min(1),
  branch: z.string().min(1),
  candidates: z.array(repositoryManifestCandidateSchema),
  warnings: z.array(z.string()),
});

export const repositoryManifestResourceSchema = z.strictObject({
  api_version: z.string(),
  kind: z.string().min(1),
  namespace: z.string().nullable(),
  name: z.string().min(1),
});

export const repositoryManifestValidationSchema = z.strictObject({
  repo_ref: z.string().min(1),
  branch: z.string().min(1),
  manifest_path: z.string().min(1),
  valid: z.boolean(),
  status: z.string(),
  validation_mode: z.string(),
  resource_count: z.number().int().nonnegative(),
  resources: z.array(repositoryManifestResourceSchema),
  warnings: z.array(z.string()),
  errors: z.array(z.string()),
});

export type RepositoryProbe = z.output<typeof repositoryProbeSchema>;
export type RepositoryBranch = z.output<typeof repositoryBranchSchema>;
export type RepositoryBranchList = z.output<typeof repositoryBranchListSchema>;
export type RepositoryManifestCandidate = z.output<typeof repositoryManifestCandidateSchema>;
export type RepositoryManifestCandidateList = z.output<typeof repositoryManifestCandidateListSchema>;
export type RepositoryManifestValidation = z.output<typeof repositoryManifestValidationSchema>;
