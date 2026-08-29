/**
 * The single place the console talks to the backend.
 *
 * The generated client in `./services` is dead code: it was produced from an
 * older spec, nothing imports it, and its types do not match what the screens
 * actually parse. Rather than regenerate it and leave two sources of truth,
 * every call goes through here, where the response shapes are declared once and
 * checked by the backend's contract tests.
 */

const BASE = '/api/v1';

export class ApiError extends Error {
  status: number;
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit, isRetry = false): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = localStorage.getItem('synapse_access_token');
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
  });

  // We want to skip refresh attempts for login and refresh endpoints themselves
  const isAuthAuthEndpoint = path.startsWith('/auth/login') || path.startsWith('/auth/refresh') || path.startsWith('/auth/logout');
  
  if (response.status === 401 && !isRetry && !isAuthAuthEndpoint) {
    try {
      const refreshRes = await fetch(`${BASE}/auth/refresh`, { method: 'POST' });
      if (refreshRes.ok) {
        const data = await refreshRes.json();
        localStorage.setItem('synapse_access_token', data.access_token);
        return request<T>(path, init, true);
      }
    } catch (e) {
      // ignore
    }
    localStorage.removeItem('synapse_access_token');
    window.location.href = '/login';
  }

  if (!response.ok) {
    // FastAPI puts the human-readable reason in `detail`; surfacing it is what
    // lets a screen show why an export was refused instead of a bare 422.
    let detail: unknown;
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body?.detail ?? body;
      if (typeof detail === 'string') message = detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) });
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' });

// --- Response shapes -------------------------------------------------------

export interface UserInfo {
  user_id: string;
  email: string;
  display_name: string;
  org_id: string;
  role_ids: string[];
  clearance: string;
  is_org_supervisor: boolean;
  active_scope_ids: string[];
  active_scope_version_ids: string[];
  session_id: string | null;
  token_version: number;
}


export interface Badges {
  curation: number;
  snapshots: number;
  snapshots_alert: boolean;
}

export interface MergeCandidate {
  id: string;
  entity_type: string;
  primary_id: string;
  primary_name: string;
  secondary_id: string;
  secondary_name: string;
  confidence: number;
  reason: string;
  status: string;
}

export interface QuarantineRecord {
  id: string;
  kind: string;
  subject: string;
  detail: string;
  created_at: string;
}

export interface Contradiction {
  id: string;
  subject: string;
  relation_type: string;
  object: string;
  fact: string;
  valid_at: string | null;
  invalid_at: string | null;
  status: string;
  group_id: string;
}

export interface CardinalityConflict {
  id: string;
  entity_name: string;
  relation_type: string;
  targets: string[];
  max_active_outgoing: number;
  detail: string;
}

export interface AclIssue {
  id: string;
  fact: string;
  access_tags: string[];
  detail: string;
}

export interface CalibrationItem {
  id: string;
  title: string;
  current_val: number;
  recommended_val: number;
  recall_impact: string;
  status: string;
}

export interface SnapshotRequest {
  factScope: string;
  reportScope: string;
  accessControlHandling: 'filter_group' | 'tag_downstream' | 'export_unrestricted';
  confirmationPhrase?: string;
}

export interface ValidationCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface SnapshotResult {
  version: string;
  date: string;
  size: string;
  size_bytes: number;
  entity_count: number;
  edge_count: number;
  report_count: number;
  removed_edges: number;
  passed: boolean;
  validation_checks: ValidationCheck[];
  policy: string;
  diff: { addedEntities: number; removedEdges: number };
}

export interface GeneralConfig {
  display_name: string;
  isolation_mode: string;
  retention_days: number;
  default_access_tag: string;
}

export interface DeliveryConfig {
  endpoint: string;
  format: string;
  notify_on_publish: boolean;
  auth_header: string;
}

export interface AclGroup {
  id: string;
  name: string;
  description: string;
}

export interface ApiKey {
  id: string;
  name: string;
  maskedKey: string;
  environment: string;
  created_at?: string;
  /** Returned only by create, and never retrievable again. */
  key?: string;
}

export interface CommunityReport {
  id: string;
  title: string;
  date: string;
  status: 'confirmed' | 'signal';
  includedInExport: boolean;
  content: string;
  entity_count: number;
  fact_count: number;
  superseded_count: number;
  members: string[];
}

export interface SimulatedFact {
  fact: string;
  relation_type: string;
  score: number;
  valid_at: string | null;
  invalid_at: string | null;
  provenance: {
    chunk_id: string;
    source_document_name: string;
    access_tag: string;
    mutation_id: string;
  }[];
}

export interface SimulationResult {
  query: string;
  resolved_entities: string[];
  retrieved_facts: SimulatedFact[];
  withheld_facts: { fact: string; reason: string }[];
  context: { facts: string[]; reports: string[] };
  fact_count: number;
  withheld_count: number;
  access_level: string;
}

export interface ExportMetrics {
  last_generation: {
    version: string;
    outcome: string;
    entity_count: number;
    edge_count: number;
    removed_edges: number;
    created_at: string;
  } | null;
  last_downstream_fetch: { version: string; fetched_at: string } | null;
  size_trend: { version: string; byte_size: number }[];
  total_snapshots: number;
}

export interface LlmTelemetryData {
  aggregate: {
    total_calls: number;
    total_tokens: number;
    total_cost_usd: number;
    total_nodes_yield: number;
    total_edges_yield: number;
    avg_latency_ms: number;
  };
  recent_calls: {
    id: string;
    episode_id: string;
    prompt_name: string;
    model_name: string;
    latency_ms: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    cost_usd: number;
    input_chars: number;
    nodes_yield: number;
    edges_yield: number;
    error: string | null;
    created_at: string;
  }[];
}

// --- Endpoints -------------------------------------------------------------

export interface EntitySearchResult {
  id: string;
  name: string;
  type: string;
}

// --- Chat types ------------------------------------------------------------

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface CitedFact {
  fact: string;
  relation_type: string | null;
  source_name: string | null;
  target_name: string | null;
}

export interface ChatResponse {
  answer: string;
  citations: CitedFact[];
  facts_used: number;
}

export interface ScopeProposal {
  id: string;
  suggested_name: string;
  description: string;
  estimated_entity_count: number;
  estimated_edge_count: number;
  status: string;
  created_at: string;
  suggested_sensitivity: string;
  confidence: number;
  sample_entities_json?: string | string[];
  source_distribution_json?: any;
  discovery_method?: string;
  relevance_score?: number;
  matched_keywords?: string[];
}

export interface Scope {
  id: string;
  name: string;
  description: string;
  status: string;
  sensitivity: string;
  review_status: string;
  active_version_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScopeVersion {
  id: string;
  version_number: number;
  status: string;
  boundary_kind: string;
  member_entity_count: number;
  member_edge_count: number;
  created_at: string;
  entity_drift_pct: number;
  edge_drift_pct: number;
}

export interface ScopeAssignment {
  id: string;
  scope_id: string;
  assignee_kind: 'role' | 'team' | 'user';
  assignee_id: string;
  granted_by: string;
  valid_from: string;
  valid_to: string | null;
  revoked_at: string | null;
  reason: string;
}

export interface Role {
  id: string;
  org_id: string;
  name: string;
  description: string;
  clearance: string;
  is_system_role: boolean;
  created_at: string;
  updated_at: string;
}

export interface Team {
  id: string;
  org_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  members?: TeamMember[];
}

export interface TeamMember {
  id: string;
  email: string;
  display_name: string;
  joined_at: string;
}

export const api = {
  badges: () => get<Badges>('/summary/badges'),

  curation: {
    queue: () => get<MergeCandidate[]>('/curation/queue'),
    merge: (id: string) => post<{ id: string; status: string }>(`/curation/queue/${id}/merge`),
    distinct: (id: string) => post<{ id: string; status: string }>(`/curation/queue/${id}/distinct`),
    quarantine: () => get<QuarantineRecord[]>('/curation/quarantine'),
    contradictions: () => get<Contradiction[]>('/curation/contradictions'),
    conflicts: () => get<CardinalityConflict[]>('/curation/conflicts'),
    aclIssues: () => get<AclIssue[]>('/curation/acl'),
    calibration: () => get<CalibrationItem[]>('/curation/calibration'),
    approveCalibration: (id: string) => post<unknown>(`/curation/calibration/${id}/approve`),
    dismissCalibration: (id: string) => post<unknown>(`/curation/calibration/${id}/dismiss`),
  },

  snapshots: {
    generate: (body: SnapshotRequest) => post<SnapshotResult>('/snapshot/generate', body),
    list: () => get<SnapshotResult[]>('/snapshots'),
    downloadUrl: (version: string) => `${BASE}/snapshots/${version}/download`,
    notify: (version: string) =>
      post<{ version: string; status: string; detail: string }>(`/snapshots/${version}/notify`),
  },

  settings: {
    general: () => get<GeneralConfig>('/settings/general'),
    saveGeneral: (body: GeneralConfig) => put<GeneralConfig>('/settings/general', body),
    delivery: () => get<DeliveryConfig>('/settings/delivery'),
    saveDelivery: (body: DeliveryConfig) => put<DeliveryConfig>('/settings/delivery', body),
    aclGroups: () => get<AclGroup[]>('/settings/acl-groups'),
    createAclGroup: (body: { name: string; description: string }) =>
      post<AclGroup>('/settings/acl-groups', body),
    deleteAclGroup: (id: string) => del<unknown>(`/settings/acl-groups/${id}`),
    apiKeys: () => get<ApiKey[]>('/settings/api-keys'),
    createApiKey: (body: { name: string; environment: string }) =>
      post<ApiKey>('/settings/api-keys', body),
    revokeApiKey: (id: string) => del<unknown>(`/settings/api-keys/${id}`),
  },

  reports: {
    list: () => get<CommunityReport[]>('/reports'),
  },

  simulate: (body: { query: string; access_level?: string }) =>
    post<SimulationResult>('/query/simulate', body),

  observability: {
    pipeline: () => get<any>('/observability/pipeline'),
    consistency: () => get<any>('/observability/consistency'),
    qualityMetrics: () => get<any>('/observability/quality-metrics'),
    exportMetrics: () => get<ExportMetrics>('/observability/export-metrics'),
    escalate: (id: string) => post<unknown>(`/observability/consistency/${id}/escalate`),
    llmTelemetry: () => get<LlmTelemetryData>('/observability/llm-telemetry'),
  },

  graph: {
    topology: (scopeVersionId?: string) => get<any>(`/graph/topology${scopeVersionId ? `?scope_version_id=${encodeURIComponent(scopeVersionId)}` : ''}`),
    search: (q: string) => get<{ results: EntitySearchResult[] }>(`/entities/search?q=${encodeURIComponent(q)}`),
    entity: (id: string) => get<any>(`/entities/${encodeURIComponent(id)}`),
  },

  github: {
    repos: () => get<any[]>('/github/repos'),
    addRepo: (owner: string, name: string) => post<any>('/github/repos', { owner, name }),
    syncRepo: (id: string) => post<any>(`/github/repos/${id}/sync`),
  },

  chat: (body: { messages: ChatMessage[]; num_facts?: number }) =>
    post<ChatResponse>('/chat', body),

  scopes: {
    list: () => get<Scope[]>('/scopes'),
    get: (id: string) => get<Scope>(`/scopes/${id}`),
    proposals: () => get<ScopeProposal[]>('/scopes/proposals'),
    generateProposals: () => post<{status: string, count: number, proposals: ScopeProposal[]}>('/scopes/proposals/generate'),
    discoverProposals: (body: { prompt: string; max_communities?: number; min_entities?: number; min_density?: number }) =>
      post<{status: string, count: number, proposals: ScopeProposal[]}>('/scopes/proposals/discover', body),
    approveProposal: (id: string) => post<{status: string, scope_id: string}>(`/scopes/proposals/${id}/approve`),
    previewProposal: (id: string, assignee_kind?: string, assignee_id?: string) => post<{nodes: any[], edges: any[]}>(`/scopes/proposals/${id}/preview${assignee_kind && assignee_id ? `?assignee_kind=${assignee_kind}&assignee_id=${assignee_id}` : ''}`),
    versions: (scopeId: string) => get<ScopeVersion[]>(`/scopes/${scopeId}/versions`),
    approveVersion: (scopeId: string, versionId: string) => post<{status: string}>(`/scopes/${scopeId}/versions/${versionId}/approve`),
    rejectVersion: (scopeId: string, versionId: string) => post<{status: string}>(`/scopes/${scopeId}/versions/${versionId}/reject`),
    assignments: (scopeId: string) => get<ScopeAssignment[]>(`/scopes/${scopeId}/assignments`),
    createAssignment: (scopeId: string, data: { assignee_kind: string; assignee_id: string; reason?: string, valid_from?: string, valid_to?: string }) => post<ScopeAssignment>(`/scopes/${scopeId}/assignments`, data),
    bulkCreateAssignments: (scopeId: string, assignments: { assignee_kind: string; assignee_id: string; reason?: string; valid_from?: string; valid_to?: string }[]) => post<ScopeAssignment[]>(`/scopes/${scopeId}/assignments/bulk`, { assignments }),
    revokeAssignment: (scopeId: string, assignmentId: string) => del<{status: string}>(`/scopes/${scopeId}/assignments/${assignmentId}`),
  },

  teams: {
    list: () => get<Team[]>('/teams'),
    get: (id: string) => get<Team>(`/teams/${id}`),
    create: (body: { name: string; description: string }) => post<Team>('/teams', body),
    update: (id: string, body: { name: string; description: string }) => request<Team>(`/teams/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    delete: (id: string) => del<{status: string}>(`/teams/${id}`),
    addMember: (teamId: string, userId: string) => post<{status: string}>(`/teams/${teamId}/members`, { user_id: userId }),
    removeMember: (teamId: string, userId: string) => del<{status: string}>(`/teams/${teamId}/members/${userId}`),
  },

  roles: {
    list: () => get<Role[]>('/roles'),
    get: (id: string) => get<Role>(`/roles/${id}`),
    create: (body: { name: string; description: string; clearance: string }) => post<Role>('/roles', body),
    update: (id: string, body: { description?: string; clearance?: string }) => request<Role>(`/roles/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    delete: (id: string) => del<{status: string}>(`/roles/${id}`),
  },

  auth: {
    login: (body: any) => post<any>('/auth/login', body),
    logout: () => post<any>('/auth/logout'),
    refresh: () => post<any>('/auth/refresh'),
    me: () => get<UserInfo>('/auth/me'),
    forgotPassword: (body: { email: string }) => post<any>('/auth/forgot-password', body),
    resetPassword: (body: { token: string; new_password: string }) => post<any>('/auth/reset-password', body),
    acceptInvitation: (token: string) => get<{ email: string }>(`/auth/accept-invitation?token=${encodeURIComponent(token)}`),
    completeSignup: (data: { email: string; passcode: string; password: string }) =>
    post<{ status: string }>('/auth/complete-signup', data),
    sessions: () => get<any[]>('/auth/sessions'),
    deleteSession: (id: string) => del<any>(`/auth/sessions/${id}`),
    deleteAllSessions: () => del<any>('/auth/sessions'),
  },

  users: {
    list: () => get<any[]>('/users'),
    get: (id: string) => get<any>(`/users/${id}`),
    invite: (body: { email: string; display_name?: string; role_ids: string[]; team_ids: string[]; passcode?: string }) => post<any>('/users/invite', body),
    update: (id: string, body: { status?: string; is_active?: number; role_ids?: string[] }) => request<any>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    revokeSessions: (id: string) => post<any>(`/users/${id}/revoke-sessions`),
  },

  mail: {
    accounts: {
      list: () => get<any[]>('/mail/accounts'),
      get: (id: string) => get<any>(`/mail/accounts/${id}`),
      update: (id: string, body: any) => request<any>(`/mail/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
      delete: (id: string) => del<void>(`/mail/accounts/${id}`),
      sync: (id: string) => post<{ status: string; account_id: string }>(`/mail/accounts/${id}/sync`),
      runs: (id: string) => get<any[]>(`/mail/accounts/${id}/runs`),
    },
    oauth: {
      gmailUrl: () => get<{ url: string }>('/mail/oauth/gmail/url'),
      gmailCallback: (code: string) => post<any>('/mail/oauth/gmail/callback', { code }),
    },
  },
};
