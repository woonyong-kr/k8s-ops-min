-- Opsia immutable pre-Alembic schema baseline.
-- Source commit: 017b2485b2c408c2f7e928379ebf6541526d32ab
-- Generated from Database.init() on PostgreSQL 17 with schema-only, no owner or ACL.
-- This file contains no application data and intentionally has no alembic_version table.

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_commands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_commands (
    command_id text NOT NULL,
    workspace_id text DEFAULT 'default'::text NOT NULL,
    correlation_id text NOT NULL,
    cluster_id text NOT NULL,
    action text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL,
    lease_id text,
    agent_id text,
    leased_until timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    result jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_policies (
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    generation integer NOT NULL,
    policy jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_policy_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_policy_status (
    id bigint NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    generation integer NOT NULL,
    status text NOT NULL,
    message text NOT NULL,
    details jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_policy_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_policy_status_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_policy_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_policy_status_id_seq OWNED BY public.agent_policy_status.id;


--
-- Name: agent_reconcile_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_reconcile_status (
    id bigint NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    generation integer NOT NULL,
    status text NOT NULL,
    message text NOT NULL,
    details jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_reconcile_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_reconcile_status_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_reconcile_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_reconcile_status_id_seq OWNED BY public.agent_reconcile_status.id;


--
-- Name: ai_conversation_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_conversation_messages (
    message_id text NOT NULL,
    conversation_id text NOT NULL,
    workspace_id text NOT NULL,
    role text NOT NULL,
    content text NOT NULL,
    agent text NOT NULL,
    correlation_id text,
    metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_conversations (
    conversation_id text NOT NULL,
    workspace_id text NOT NULL,
    user_id text NOT NULL,
    title text NOT NULL,
    agent text NOT NULL,
    status text NOT NULL,
    context jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alert_channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_channels (
    channel_id text NOT NULL,
    workspace_id text NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    url text NOT NULL,
    min_severity text NOT NULL,
    enabled boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: applications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.applications (
    application_id text NOT NULL,
    workspace_id text NOT NULL,
    repository_id text NOT NULL,
    name text NOT NULL,
    manifest_path text NOT NULL,
    status text NOT NULL,
    metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approvals (
    approval_id text NOT NULL,
    workflow_run_id text NOT NULL,
    workspace_id text NOT NULL,
    application_id text NOT NULL,
    binding_id text NOT NULL,
    environment text NOT NULL,
    status text NOT NULL,
    reason text NOT NULL,
    requested_role text NOT NULL,
    requested_by text,
    decided_by text,
    decision text,
    details jsonb NOT NULL,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id bigint NOT NULL,
    event_id text NOT NULL,
    subject text NOT NULL,
    source text NOT NULL,
    correlation_id text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


--
-- Name: catalog_install_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_install_runs (
    install_id text NOT NULL,
    workspace_id text NOT NULL,
    item_id text NOT NULL,
    version text NOT NULL,
    cluster_id text NOT NULL,
    namespace text NOT NULL,
    application_name text NOT NULL,
    status text NOT NULL,
    requested_by text NOT NULL,
    "values" jsonb NOT NULL,
    plan jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: catalog_item_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_item_versions (
    version_id text NOT NULL,
    item_id text NOT NULL,
    version text NOT NULL,
    package_type text NOT NULL,
    package_ref text NOT NULL,
    values_schema jsonb NOT NULL,
    template jsonb NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: catalog_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_items (
    item_id text NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    description text NOT NULL,
    default_version text NOT NULL,
    status text NOT NULL,
    metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cluster_agent_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_agent_status (
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    agent_id text NOT NULL,
    status text NOT NULL,
    capabilities jsonb NOT NULL,
    details jsonb NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cluster_inventory_resources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_inventory_resources (
    inventory_key text NOT NULL,
    snapshot_id text NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    resource_type text NOT NULL,
    api_version text NOT NULL,
    kind text NOT NULL,
    namespace text,
    name text NOT NULL,
    uid text,
    resource_version text,
    status text NOT NULL,
    health text NOT NULL,
    labels jsonb NOT NULL,
    annotations jsonb NOT NULL,
    summary jsonb NOT NULL,
    raw jsonb NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cluster_inventory_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_inventory_snapshots (
    snapshot_id text NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    agent_id text NOT NULL,
    source text NOT NULL,
    status text NOT NULL,
    collected_at timestamp with time zone NOT NULL,
    resource_count integer NOT NULL,
    summary jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cluster_registrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_registrations (
    id bigint NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    name text NOT NULL,
    environment text NOT NULL,
    status text NOT NULL,
    agent_token_hash text,
    settings jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cluster_registrations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_registrations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_registrations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_registrations_id_seq OWNED BY public.cluster_registrations.id;


--
-- Name: cluster_usage_samples; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_usage_samples (
    id bigint NOT NULL,
    snapshot_id text NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    sampled_at timestamp with time zone NOT NULL,
    usage jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cluster_usage_samples_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_usage_samples_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_usage_samples_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_usage_samples_id_seq OWNED BY public.cluster_usage_samples.id;


--
-- Name: deployment_bindings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deployment_bindings (
    binding_id text NOT NULL,
    workspace_id text NOT NULL,
    repository_id text NOT NULL,
    watch_target_id text,
    cluster_id text NOT NULL,
    namespace text NOT NULL,
    app_name text NOT NULL,
    manifest_path text NOT NULL,
    environment text NOT NULL,
    resource_class text NOT NULL,
    status text NOT NULL,
    deploy_policy jsonb NOT NULL,
    access_policy jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: event_dead_letters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_dead_letters (
    id bigint NOT NULL,
    original_event_id text NOT NULL,
    original_subject text NOT NULL,
    consumer text NOT NULL,
    correlation_id text NOT NULL,
    attempts integer NOT NULL,
    error text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL,
    replayed_at timestamp with time zone,
    replay_event_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: event_dead_letters_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_dead_letters_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_dead_letters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_dead_letters_id_seq OWNED BY public.event_dead_letters.id;


--
-- Name: event_processing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_processing (
    event_id text NOT NULL,
    consumer text NOT NULL,
    subject text NOT NULL,
    correlation_id text NOT NULL,
    status text NOT NULL,
    attempts integer NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    event_id text NOT NULL,
    subject text NOT NULL,
    source text NOT NULL,
    correlation_id text NOT NULL,
    causation_id text,
    payload jsonb NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence (
    id bigint NOT NULL,
    workspace_id text DEFAULT 'default'::text NOT NULL,
    correlation_id text NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: evidence_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.evidence_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evidence_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.evidence_id_seq OWNED BY public.evidence.id;


--
-- Name: evidence_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence_jobs (
    job_id text NOT NULL,
    evidence_key text NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    source_id text NOT NULL,
    provider_key text NOT NULL,
    window_start text NOT NULL,
    policy_generation integer NOT NULL,
    provider_policy jsonb NOT NULL,
    status text NOT NULL,
    lease_id text,
    agent_id text,
    leased_until timestamp with time zone,
    attempt_count integer NOT NULL,
    max_attempts integer NOT NULL,
    failure_policy text NOT NULL,
    result jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: evidence_windows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence_windows (
    evidence_key text NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    source_id text NOT NULL,
    window_start text NOT NULL,
    agent_id text,
    event_id text NOT NULL,
    correlation_id text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: git_repositories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.git_repositories (
    repository_id text NOT NULL,
    workspace_id text NOT NULL,
    provider text NOT NULL,
    repo_ref text NOT NULL,
    default_branch text NOT NULL,
    credential_ref text,
    status text NOT NULL,
    access_policy jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: git_watch_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.git_watch_targets (
    watch_target_id text NOT NULL,
    workspace_id text NOT NULL,
    repository_id text NOT NULL,
    branch text NOT NULL,
    manifest_path text NOT NULL,
    interval_seconds bigint NOT NULL,
    last_seen_commit_sha text,
    last_polled_at timestamp with time zone,
    status text NOT NULL,
    settings jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.group_members (
    id bigint NOT NULL,
    group_id text NOT NULL,
    user_id text NOT NULL,
    role text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: group_members_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.group_members_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: group_members_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.group_members_id_seq OWNED BY public.group_members.id;


--
-- Name: groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.groups (
    group_id text NOT NULL,
    organization_id text NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: manifest_artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manifest_artifacts (
    artifact_id text NOT NULL,
    workspace_id text DEFAULT 'default'::text NOT NULL,
    repository_id text NOT NULL,
    watch_target_id text,
    binding_id text NOT NULL,
    commit_sha text NOT NULL,
    manifest_path text NOT NULL,
    status text NOT NULL,
    status_reason text,
    rendered_manifest jsonb,
    source_summary jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: member_resource_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.member_resource_roles (
    id bigint NOT NULL,
    resource_assignment_id text NOT NULL,
    user_id text NOT NULL,
    role text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: member_resource_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.member_resource_roles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: member_resource_roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.member_resource_roles_id_seq OWNED BY public.member_resource_roles.id;


--
-- Name: metric_query_presets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.metric_query_presets (
    preset_id text NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    source text NOT NULL,
    query text NOT NULL,
    range_seconds bigint,
    step_seconds bigint,
    unit text NOT NULL,
    created_by text NOT NULL,
    metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: metric_widgets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.metric_widgets (
    widget_id text NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    query_preset_id text NOT NULL,
    title text NOT NULL,
    kind text NOT NULL,
    "position" jsonb NOT NULL,
    settings jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: organization_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organization_members (
    id bigint NOT NULL,
    organization_id text NOT NULL,
    user_id text NOT NULL,
    role text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: organization_members_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.organization_members_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: organization_members_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.organization_members_id_seq OWNED BY public.organization_members.id;


--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    organization_id text NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbox (
    id bigint NOT NULL,
    event_id text NOT NULL,
    subject text NOT NULL,
    source text NOT NULL,
    correlation_id text NOT NULL,
    causation_id text,
    occurred_at text NOT NULL,
    payload jsonb NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    lease_id text,
    leased_until timestamp with time zone,
    sent_at timestamp with time zone
);


--
-- Name: outbox_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.outbox_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: outbox_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.outbox_id_seq OWNED BY public.outbox.id;


--
-- Name: pull_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pull_requests (
    id bigint NOT NULL,
    correlation_id text NOT NULL,
    pr_url text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pull_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pull_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pull_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pull_requests_id_seq OWNED BY public.pull_requests.id;


--
-- Name: rca_backlog_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rca_backlog_items (
    backlog_id text NOT NULL,
    workspace_id text NOT NULL,
    incident_id text NOT NULL,
    symptom text NOT NULL,
    title text NOT NULL,
    reason text NOT NULL,
    evidence_ref text NOT NULL,
    missing_evidence jsonb NOT NULL,
    status text NOT NULL,
    occurrence_count integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rca_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rca_reports (
    id bigint NOT NULL,
    workspace_id text DEFAULT 'default'::text NOT NULL,
    correlation_id text NOT NULL,
    root_cause text NOT NULL,
    action text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rca_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rca_reports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rca_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rca_reports_id_seq OWNED BY public.rca_reports.id;


--
-- Name: rca_timeline; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rca_timeline (
    id bigint NOT NULL,
    workspace_id text NOT NULL,
    correlation_id text NOT NULL,
    cluster_id text,
    incident_id text,
    evidence_ref text,
    current_subject text NOT NULL,
    status text NOT NULL,
    root_cause text,
    confidence double precision,
    supporting_evidence jsonb,
    missing_evidence jsonb,
    action_route text,
    command_id text,
    pr_url text,
    error_reason text,
    last_event_id text NOT NULL,
    last_event_at text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rca_timeline_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rca_timeline_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rca_timeline_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rca_timeline_id_seq OWNED BY public.rca_timeline.id;


--
-- Name: recovery_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recovery_plans (
    id bigint NOT NULL,
    plan_id text NOT NULL,
    workspace_id text NOT NULL,
    correlation_id text NOT NULL,
    incident_id text NOT NULL,
    evidence_ref text NOT NULL,
    status text NOT NULL,
    selected_action_id text,
    selected_by text,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: recovery_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.recovery_plans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: recovery_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.recovery_plans_id_seq OWNED BY public.recovery_plans.id;


--
-- Name: repo_changes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.repo_changes (
    id bigint NOT NULL,
    workspace_id text DEFAULT 'default'::text NOT NULL,
    correlation_id text NOT NULL,
    commit_sha text NOT NULL,
    repository_id text,
    watch_target_id text,
    binding_id text,
    manifest_path text,
    manifest jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: repo_changes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.repo_changes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: repo_changes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.repo_changes_id_seq OWNED BY public.repo_changes.id;


--
-- Name: resource_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_assignments (
    resource_assignment_id text NOT NULL,
    organization_id text NOT NULL,
    group_id text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: role_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_permissions (
    id bigint NOT NULL,
    organization_id text DEFAULT '__global__'::text NOT NULL,
    resource_type text NOT NULL,
    role text NOT NULL,
    permission text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: role_permissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.role_permissions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: role_permissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.role_permissions_id_seq OWNED BY public.role_permissions.id;


--
-- Name: target_desired_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.target_desired_states (
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    component text NOT NULL,
    namespace text NOT NULL,
    version text NOT NULL,
    status text NOT NULL,
    updated_by text,
    spec jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: target_reconcile_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.target_reconcile_records (
    reconcile_id text NOT NULL,
    workspace_id text NOT NULL,
    cluster_id text NOT NULL,
    desired_state_version text NOT NULL,
    status text NOT NULL,
    drifted boolean NOT NULL,
    applied boolean NOT NULL,
    message text NOT NULL,
    details jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_accounts (
    user_id text NOT NULL,
    email text,
    password_hash text,
    display_name text NOT NULL,
    status text NOT NULL,
    role text DEFAULT 'user'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: workflow_run_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_run_steps (
    step_id text NOT NULL,
    workflow_run_id text NOT NULL,
    workspace_id text NOT NULL,
    application_id text NOT NULL,
    binding_id text NOT NULL,
    environment text NOT NULL,
    name text NOT NULL,
    status text NOT NULL,
    message text,
    details jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: workflow_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_runs (
    workflow_run_id text NOT NULL,
    workspace_id text NOT NULL,
    application_id text NOT NULL,
    binding_id text NOT NULL,
    environment text NOT NULL,
    cluster_id text NOT NULL,
    commit_sha text NOT NULL,
    status text NOT NULL,
    current_step text NOT NULL,
    summary text,
    command_id text,
    metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: workspaces; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workspaces (
    workspace_id text NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_policy_status id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_policy_status ALTER COLUMN id SET DEFAULT nextval('public.agent_policy_status_id_seq'::regclass);


--
-- Name: agent_reconcile_status id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reconcile_status ALTER COLUMN id SET DEFAULT nextval('public.agent_reconcile_status_id_seq'::regclass);


--
-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


--
-- Name: cluster_registrations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_registrations ALTER COLUMN id SET DEFAULT nextval('public.cluster_registrations_id_seq'::regclass);


--
-- Name: cluster_usage_samples id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_usage_samples ALTER COLUMN id SET DEFAULT nextval('public.cluster_usage_samples_id_seq'::regclass);


--
-- Name: event_dead_letters id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_dead_letters ALTER COLUMN id SET DEFAULT nextval('public.event_dead_letters_id_seq'::regclass);


--
-- Name: evidence id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence ALTER COLUMN id SET DEFAULT nextval('public.evidence_id_seq'::regclass);


--
-- Name: group_members id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_members ALTER COLUMN id SET DEFAULT nextval('public.group_members_id_seq'::regclass);


--
-- Name: member_resource_roles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_resource_roles ALTER COLUMN id SET DEFAULT nextval('public.member_resource_roles_id_seq'::regclass);


--
-- Name: organization_members id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members ALTER COLUMN id SET DEFAULT nextval('public.organization_members_id_seq'::regclass);


--
-- Name: outbox id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox ALTER COLUMN id SET DEFAULT nextval('public.outbox_id_seq'::regclass);


--
-- Name: pull_requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pull_requests ALTER COLUMN id SET DEFAULT nextval('public.pull_requests_id_seq'::regclass);


--
-- Name: rca_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rca_reports ALTER COLUMN id SET DEFAULT nextval('public.rca_reports_id_seq'::regclass);


--
-- Name: rca_timeline id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rca_timeline ALTER COLUMN id SET DEFAULT nextval('public.rca_timeline_id_seq'::regclass);


--
-- Name: recovery_plans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovery_plans ALTER COLUMN id SET DEFAULT nextval('public.recovery_plans_id_seq'::regclass);


--
-- Name: repo_changes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repo_changes ALTER COLUMN id SET DEFAULT nextval('public.repo_changes_id_seq'::regclass);


--
-- Name: role_permissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions ALTER COLUMN id SET DEFAULT nextval('public.role_permissions_id_seq'::regclass);


--
-- Name: agent_commands agent_commands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_commands
    ADD CONSTRAINT agent_commands_pkey PRIMARY KEY (command_id);


--
-- Name: agent_policies agent_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_policies
    ADD CONSTRAINT agent_policies_pkey PRIMARY KEY (workspace_id, cluster_id);


--
-- Name: agent_policy_status agent_policy_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_policy_status
    ADD CONSTRAINT agent_policy_status_pkey PRIMARY KEY (id);


--
-- Name: agent_reconcile_status agent_reconcile_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reconcile_status
    ADD CONSTRAINT agent_reconcile_status_pkey PRIMARY KEY (id);


--
-- Name: ai_conversation_messages ai_conversation_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversation_messages
    ADD CONSTRAINT ai_conversation_messages_pkey PRIMARY KEY (message_id);


--
-- Name: ai_conversations ai_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversations
    ADD CONSTRAINT ai_conversations_pkey PRIMARY KEY (conversation_id);


--
-- Name: alert_channels alert_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_channels
    ADD CONSTRAINT alert_channels_pkey PRIMARY KEY (channel_id);


--
-- Name: applications applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT applications_pkey PRIMARY KEY (application_id);


--
-- Name: applications applications_workspace_id_repository_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT applications_workspace_id_repository_id_name_key UNIQUE (workspace_id, repository_id, name);


--
-- Name: approvals approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_pkey PRIMARY KEY (approval_id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: catalog_install_runs catalog_install_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_install_runs
    ADD CONSTRAINT catalog_install_runs_pkey PRIMARY KEY (install_id);


--
-- Name: catalog_item_versions catalog_item_versions_item_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_item_versions
    ADD CONSTRAINT catalog_item_versions_item_id_version_key UNIQUE (item_id, version);


--
-- Name: catalog_item_versions catalog_item_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_item_versions
    ADD CONSTRAINT catalog_item_versions_pkey PRIMARY KEY (version_id);


--
-- Name: catalog_items catalog_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_items
    ADD CONSTRAINT catalog_items_pkey PRIMARY KEY (item_id);


--
-- Name: catalog_items catalog_items_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_items
    ADD CONSTRAINT catalog_items_slug_key UNIQUE (slug);


--
-- Name: cluster_agent_status cluster_agent_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_agent_status
    ADD CONSTRAINT cluster_agent_status_pkey PRIMARY KEY (workspace_id, cluster_id, agent_id);


--
-- Name: cluster_inventory_resources cluster_inventory_resources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_inventory_resources
    ADD CONSTRAINT cluster_inventory_resources_pkey PRIMARY KEY (inventory_key);


--
-- Name: cluster_inventory_snapshots cluster_inventory_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_inventory_snapshots
    ADD CONSTRAINT cluster_inventory_snapshots_pkey PRIMARY KEY (snapshot_id);


--
-- Name: cluster_registrations cluster_registrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_registrations
    ADD CONSTRAINT cluster_registrations_pkey PRIMARY KEY (id);


--
-- Name: cluster_registrations cluster_registrations_workspace_id_cluster_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_registrations
    ADD CONSTRAINT cluster_registrations_workspace_id_cluster_id_key UNIQUE (workspace_id, cluster_id);


--
-- Name: cluster_usage_samples cluster_usage_samples_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_usage_samples
    ADD CONSTRAINT cluster_usage_samples_pkey PRIMARY KEY (id);


--
-- Name: deployment_bindings deployment_bindings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployment_bindings
    ADD CONSTRAINT deployment_bindings_pkey PRIMARY KEY (binding_id);


--
-- Name: deployment_bindings deployment_bindings_workspace_id_repository_id_cluster_id_n_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployment_bindings
    ADD CONSTRAINT deployment_bindings_workspace_id_repository_id_cluster_id_n_key UNIQUE (workspace_id, repository_id, cluster_id, namespace, app_name);


--
-- Name: event_dead_letters event_dead_letters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_dead_letters
    ADD CONSTRAINT event_dead_letters_pkey PRIMARY KEY (id);


--
-- Name: event_processing event_processing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_processing
    ADD CONSTRAINT event_processing_pkey PRIMARY KEY (event_id, consumer);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (event_id);


--
-- Name: evidence_jobs evidence_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence_jobs
    ADD CONSTRAINT evidence_jobs_pkey PRIMARY KEY (job_id);


--
-- Name: evidence evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_pkey PRIMARY KEY (id);


--
-- Name: evidence_windows evidence_windows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence_windows
    ADD CONSTRAINT evidence_windows_pkey PRIMARY KEY (evidence_key);


--
-- Name: git_repositories git_repositories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.git_repositories
    ADD CONSTRAINT git_repositories_pkey PRIMARY KEY (repository_id);


--
-- Name: git_repositories git_repositories_workspace_id_repo_ref_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.git_repositories
    ADD CONSTRAINT git_repositories_workspace_id_repo_ref_key UNIQUE (workspace_id, repo_ref);


--
-- Name: git_watch_targets git_watch_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.git_watch_targets
    ADD CONSTRAINT git_watch_targets_pkey PRIMARY KEY (watch_target_id);


--
-- Name: git_watch_targets git_watch_targets_workspace_id_repository_id_branch_manifes_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.git_watch_targets
    ADD CONSTRAINT git_watch_targets_workspace_id_repository_id_branch_manifes_key UNIQUE (workspace_id, repository_id, branch, manifest_path);


--
-- Name: group_members group_members_group_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_members
    ADD CONSTRAINT group_members_group_id_user_id_key UNIQUE (group_id, user_id);


--
-- Name: group_members group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_members
    ADD CONSTRAINT group_members_pkey PRIMARY KEY (id);


--
-- Name: groups groups_organization_id_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_organization_id_slug_key UNIQUE (organization_id, slug);


--
-- Name: groups groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_pkey PRIMARY KEY (group_id);


--
-- Name: manifest_artifacts manifest_artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manifest_artifacts
    ADD CONSTRAINT manifest_artifacts_pkey PRIMARY KEY (artifact_id);


--
-- Name: member_resource_roles member_resource_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_resource_roles
    ADD CONSTRAINT member_resource_roles_pkey PRIMARY KEY (id);


--
-- Name: member_resource_roles member_resource_roles_resource_assignment_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_resource_roles
    ADD CONSTRAINT member_resource_roles_resource_assignment_id_user_id_key UNIQUE (resource_assignment_id, user_id);


--
-- Name: metric_query_presets metric_query_presets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_query_presets
    ADD CONSTRAINT metric_query_presets_pkey PRIMARY KEY (preset_id);


--
-- Name: metric_query_presets metric_query_presets_workspace_id_cluster_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_query_presets
    ADD CONSTRAINT metric_query_presets_workspace_id_cluster_id_name_key UNIQUE (workspace_id, cluster_id, name);


--
-- Name: metric_widgets metric_widgets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_widgets
    ADD CONSTRAINT metric_widgets_pkey PRIMARY KEY (widget_id);


--
-- Name: metric_widgets metric_widgets_workspace_id_cluster_id_title_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_widgets
    ADD CONSTRAINT metric_widgets_workspace_id_cluster_id_title_key UNIQUE (workspace_id, cluster_id, title);


--
-- Name: organization_members organization_members_organization_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_organization_id_user_id_key UNIQUE (organization_id, user_id);


--
-- Name: organization_members organization_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_pkey PRIMARY KEY (id);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (organization_id);


--
-- Name: organizations organizations_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_slug_key UNIQUE (slug);


--
-- Name: outbox outbox_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox
    ADD CONSTRAINT outbox_event_id_key UNIQUE (event_id);


--
-- Name: outbox outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox
    ADD CONSTRAINT outbox_pkey PRIMARY KEY (id);


--
-- Name: pull_requests pull_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pull_requests
    ADD CONSTRAINT pull_requests_pkey PRIMARY KEY (id);


--
-- Name: rca_backlog_items rca_backlog_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rca_backlog_items
    ADD CONSTRAINT rca_backlog_items_pkey PRIMARY KEY (backlog_id);


--
-- Name: rca_reports rca_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rca_reports
    ADD CONSTRAINT rca_reports_pkey PRIMARY KEY (id);


--
-- Name: rca_timeline rca_timeline_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rca_timeline
    ADD CONSTRAINT rca_timeline_pkey PRIMARY KEY (id);


--
-- Name: rca_timeline rca_timeline_workspace_id_correlation_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rca_timeline
    ADD CONSTRAINT rca_timeline_workspace_id_correlation_id_key UNIQUE (workspace_id, correlation_id);


--
-- Name: recovery_plans recovery_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovery_plans
    ADD CONSTRAINT recovery_plans_pkey PRIMARY KEY (id);


--
-- Name: recovery_plans recovery_plans_workspace_id_plan_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovery_plans
    ADD CONSTRAINT recovery_plans_workspace_id_plan_id_key UNIQUE (workspace_id, plan_id);


--
-- Name: repo_changes repo_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repo_changes
    ADD CONSTRAINT repo_changes_pkey PRIMARY KEY (id);


--
-- Name: resource_assignments resource_assignments_group_id_resource_type_resource_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_assignments
    ADD CONSTRAINT resource_assignments_group_id_resource_type_resource_id_key UNIQUE (group_id, resource_type, resource_id);


--
-- Name: resource_assignments resource_assignments_organization_id_resource_type_resource_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_assignments
    ADD CONSTRAINT resource_assignments_organization_id_resource_type_resource_key UNIQUE (organization_id, resource_type, resource_id);


--
-- Name: resource_assignments resource_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_assignments
    ADD CONSTRAINT resource_assignments_pkey PRIMARY KEY (resource_assignment_id);


--
-- Name: role_permissions role_permissions_organization_id_resource_type_role_permiss_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_organization_id_resource_type_role_permiss_key UNIQUE (organization_id, resource_type, role, permission);


--
-- Name: role_permissions role_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_pkey PRIMARY KEY (id);


--
-- Name: target_desired_states target_desired_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.target_desired_states
    ADD CONSTRAINT target_desired_states_pkey PRIMARY KEY (workspace_id, cluster_id, component);


--
-- Name: target_reconcile_records target_reconcile_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.target_reconcile_records
    ADD CONSTRAINT target_reconcile_records_pkey PRIMARY KEY (reconcile_id);


--
-- Name: user_accounts user_accounts_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_accounts
    ADD CONSTRAINT user_accounts_email_key UNIQUE (email);


--
-- Name: user_accounts user_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_accounts
    ADD CONSTRAINT user_accounts_pkey PRIMARY KEY (user_id);


--
-- Name: manifest_artifacts ux_manifest_artifacts_workspace_binding_commit_path; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manifest_artifacts
    ADD CONSTRAINT ux_manifest_artifacts_workspace_binding_commit_path UNIQUE (workspace_id, binding_id, commit_sha, manifest_path);


--
-- Name: workflow_run_steps workflow_run_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_run_steps
    ADD CONSTRAINT workflow_run_steps_pkey PRIMARY KEY (step_id);


--
-- Name: workflow_run_steps workflow_run_steps_workflow_run_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_run_steps
    ADD CONSTRAINT workflow_run_steps_workflow_run_id_name_key UNIQUE (workflow_run_id, name);


--
-- Name: workflow_runs workflow_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_runs
    ADD CONSTRAINT workflow_runs_pkey PRIMARY KEY (workflow_run_id);


--
-- Name: workspaces workspaces_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_pkey PRIMARY KEY (workspace_id);


--
-- Name: workspaces workspaces_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_slug_key UNIQUE (slug);


--
-- Name: ix_agent_commands_available; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_commands_available ON public.agent_commands USING btree (workspace_id, cluster_id, status, created_at) WHERE (status = ANY (ARRAY['queued'::text, 'leased'::text, 'running'::text]));


--
-- Name: ix_alert_channels_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_channels_scope ON public.alert_channels USING btree (workspace_id, enabled);


--
-- Name: ix_catalog_install_runs_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_install_runs_scope ON public.catalog_install_runs USING btree (workspace_id, cluster_id, created_at);


--
-- Name: ix_cluster_agent_status_last_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_agent_status_last_seen ON public.cluster_agent_status USING btree (workspace_id, cluster_id, last_seen_at);


--
-- Name: ix_cluster_registrations_agent_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_registrations_agent_token_hash ON public.cluster_registrations USING btree (agent_token_hash);


--
-- Name: ix_cluster_usage_samples_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_usage_samples_scope ON public.cluster_usage_samples USING btree (workspace_id, cluster_id, sampled_at);


--
-- Name: ix_event_dead_letters_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_dead_letters_open ON public.event_dead_letters USING btree (status, id) WHERE (status = 'open'::text);


--
-- Name: ix_event_processing_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_processing_status ON public.event_processing USING btree (status);


--
-- Name: ix_events_correlation_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_correlation_created ON public.events USING btree (correlation_id, created_at);


--
-- Name: ix_evidence_jobs_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_jobs_claim ON public.evidence_jobs USING btree (workspace_id, cluster_id, provider_key, status, created_at);


--
-- Name: ix_evidence_jobs_window; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_jobs_window ON public.evidence_jobs USING btree (evidence_key);


--
-- Name: ix_inventory_resources_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_resources_deleted ON public.cluster_inventory_resources USING btree (workspace_id, cluster_id, deleted_at);


--
-- Name: ix_inventory_resources_health; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_resources_health ON public.cluster_inventory_resources USING btree (workspace_id, cluster_id, health);


--
-- Name: ix_inventory_resources_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_resources_scope ON public.cluster_inventory_resources USING btree (workspace_id, cluster_id, resource_type, namespace, name);


--
-- Name: ix_inventory_snapshots_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_snapshots_scope ON public.cluster_inventory_snapshots USING btree (workspace_id, cluster_id, created_at);


--
-- Name: ix_outbox_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_claim ON public.outbox USING btree (source, sent_at, leased_until, id);


--
-- Name: ix_rca_timeline_open_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rca_timeline_open_cluster ON public.rca_timeline USING btree (workspace_id, cluster_id, status, incident_id);


--
-- Name: ix_rca_timeline_scope_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rca_timeline_scope_updated ON public.rca_timeline USING btree (workspace_id, updated_at);


--
-- Name: ux_role_permissions_organization_resource_role_permission; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_role_permissions_organization_resource_role_permission ON public.role_permissions USING btree (organization_id, resource_type, role, permission);


--
-- Name: ux_user_accounts_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_user_accounts_email ON public.user_accounts USING btree (email) WHERE (email IS NOT NULL);


--
-- Name: ai_conversation_messages ai_conversation_messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversation_messages
    ADD CONSTRAINT ai_conversation_messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.ai_conversations(conversation_id) ON DELETE CASCADE;


--
-- Name: applications applications_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT applications_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- Name: cluster_registrations cluster_registrations_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_registrations
    ADD CONSTRAINT cluster_registrations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- Name: deployment_bindings deployment_bindings_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployment_bindings
    ADD CONSTRAINT deployment_bindings_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- Name: git_repositories git_repositories_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.git_repositories
    ADD CONSTRAINT git_repositories_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- Name: git_watch_targets git_watch_targets_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.git_watch_targets
    ADD CONSTRAINT git_watch_targets_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- Name: group_members group_members_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_members
    ADD CONSTRAINT group_members_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(group_id);


--
-- Name: group_members group_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_members
    ADD CONSTRAINT group_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_accounts(user_id);


--
-- Name: groups groups_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(organization_id);


--
-- Name: manifest_artifacts manifest_artifacts_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manifest_artifacts
    ADD CONSTRAINT manifest_artifacts_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- Name: member_resource_roles member_resource_roles_resource_assignment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_resource_roles
    ADD CONSTRAINT member_resource_roles_resource_assignment_id_fkey FOREIGN KEY (resource_assignment_id) REFERENCES public.resource_assignments(resource_assignment_id);


--
-- Name: member_resource_roles member_resource_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_resource_roles
    ADD CONSTRAINT member_resource_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_accounts(user_id);


--
-- Name: metric_query_presets metric_query_presets_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_query_presets
    ADD CONSTRAINT metric_query_presets_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- Name: metric_widgets metric_widgets_query_preset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_widgets
    ADD CONSTRAINT metric_widgets_query_preset_id_fkey FOREIGN KEY (query_preset_id) REFERENCES public.metric_query_presets(preset_id) ON DELETE CASCADE;


--
-- Name: metric_widgets metric_widgets_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_widgets
    ADD CONSTRAINT metric_widgets_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- Name: organization_members organization_members_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(organization_id);


--
-- Name: organization_members organization_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_accounts(user_id);


--
-- Name: resource_assignments resource_assignments_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_assignments
    ADD CONSTRAINT resource_assignments_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(group_id);


--
-- Name: resource_assignments resource_assignments_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_assignments
    ADD CONSTRAINT resource_assignments_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(organization_id);


--
-- Name: workflow_runs workflow_runs_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_runs
    ADD CONSTRAINT workflow_runs_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(workspace_id);


--
-- PostgreSQL database dump complete
--
