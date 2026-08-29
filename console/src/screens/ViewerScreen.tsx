import * as React from 'react';
import { Search, Info, Copy, FileEdit, ToggleRight, Terminal } from 'lucide-react';
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d';
// @ts-ignore
import { forceCollide, forceRadial } from 'd3-force-3d';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { useProvenance } from '../components/provenance/ProvenanceContext';
import { api, type SimulationResult } from '../api/client';

export interface GraphNode {
  id: string;
  name: string; // Compatibility
  val: number; // Compatibility
  type?: string; // Compatibility
  group_id?: string; // Compatibility
  source_id?: string; // Compatibility
  source_type?: string; // Compatibility
  need_attention?: boolean; // Compatibility
  summary?: string; // Compatibility
  x?: number;
  y?: number;
  
  // Canonical Node Schema
  identity?: {
    name: string;
    entity_type: string;
    subtype?: string;
  };
  source?: {
    source_type: string;
    source_id: string;
    external_id?: string;
    external_url?: string;
  };
  classification?: {
    domain?: string;
    category?: string;
    type?: string;
    subtype?: string;
    tags: string[];
  };
  semantic?: {
    summary?: string;
    description?: string;
  };
  attention?: {
    required: boolean;
    type: string;
    priority?: string;
    reason?: string;
    recommended_agent?: string;
    recommended_action?: string;
    requires_human_approval: boolean;
  };
  source_metadata?: {
    github?: any;
    mail?: any;
    document?: any;
  };
}

export interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  relation_type?: string;
  group_id?: string;
  source_id?: string;
  source_type?: string;
}

export const SOURCE_COLORS: Record<string, string> = {
  upload: '#eab308',    // Yellow
  github: '#18181b',    // Black
  mail: '#2563eb',      // Blue
  default: '#ea08b9ff',   // Yellow
};

export function getNodeSourceType(node: GraphNode): string {
  const rawSource = (node.source_type || node.source?.source_type || '').toLowerCase();
  if (rawSource === 'github' || rawSource === 'git') return 'github';
  if (rawSource === 'mail' || rawSource === 'email' || rawSource === 'gmail') return 'mail';
  if (rawSource === 'upload' || rawSource === 'document' || rawSource === 'file' || rawSource === 'pdf') return 'upload';

  // Infer from node type or attributes if source_type isn't explicitly set
  const type = (node.type || node.identity?.entity_type || '').toLowerCase();
  if (
    type.startsWith('git') ||
    type === 'repository' ||
    type === 'developer' ||
    type === 'codeclass' ||
    type === 'codefunction' ||
    type === 'codemethod' ||
    type === 'codevariable'
  ) {
    return 'github';
  }
  if (type.includes('mail') || type.includes('email') || type.includes('thread') || type.includes('message')) {
    return 'mail';
  }

  return 'upload';
}

export function getNodeColor(node: GraphNode): string {
  const needsAttn = node.attention?.required || node.need_attention;
  if (needsAttn) return '#ef4444';
  const st = getNodeSourceType(node);
  return SOURCE_COLORS[st] || SOURCE_COLORS.upload;
}

function nodeId(end: string | GraphNode): string {
  return typeof end === 'string' ? end : end.id;
}


function isSkillOrTechNode(n: GraphNode): boolean {
  const type = (n?.type || '').toLowerCase();
  const skillTypes = [
    'skill', 'technology', 'tech', 'framework', 'library', 'language',
    'tool', 'topic', 'concept', 'codeclass', 'codefunction', 'codemethod',
    'codevariable'
  ];
  if (skillTypes.some(st => type.includes(st))) return true;
  return false;
}

function filterGraph(
  data: { nodes?: GraphNode[]; links?: GraphLink[] },
  opts: { showSkills: boolean; search: string; source: string }
) {
  let nodes = data?.nodes || [];
  let links = data?.links || [];

  if (opts.source !== 'all') {
    nodes = nodes.filter(n => n.source_type === opts.source);
    const validNodeIds = new Set(nodes.map(n => n.id));
    links = links.filter(l => l.source_type === opts.source && validNodeIds.has(nodeId(l.source)) && validNodeIds.has(nodeId(l.target)));
  }

  const totalBeforeSkillFilter = nodes.length;

  if (!opts.showSkills) {
    nodes = nodes.filter(n => !isSkillOrTechNode(n));
    const validNodeIds = new Set(nodes.map(n => n.id));
    links = links.filter(l => validNodeIds.has(nodeId(l.source)) && validNodeIds.has(nodeId(l.target)));
  }

  const hiddenSkills = totalBeforeSkillFilter - nodes.length;

  const q = opts.search.trim().toLowerCase();
  if (!q) return { nodes, links, hiddenSkills };

  const matched = new Set(
    nodes
      .filter(
        (n) =>
          (n?.name || '').toLowerCase().includes(q) ||
          (n?.type || '').toLowerCase().includes(q)
      )
      .map((n) => n.id)
  );
  for (const link of links) {
    const s = nodeId(link.source);
    const t = nodeId(link.target);
    if (matched.has(s) || matched.has(t)) {
      matched.add(s);
      matched.add(t);
    }
  }
  return {
    nodes: nodes.filter((n) => matched.has(n.id)),
    links: links.filter(
      (l) => matched.has(nodeId(l.source)) && matched.has(nodeId(l.target))
    ),
    hiddenSkills,
  };
}

export interface EntityFact {
  id: string;
  predicate: string;
  value: string;
  linkedEntityId?: string;
  provenance: {
    sourceChunkText: string;
    sourceDocumentName: string;
    accessTag: string;
    extractionConfidence: number;
    resolutionConfidence: number;
    mutationId: string;
    snapshotVersion: string;
  };
}



export interface CommunityReportItem {
  id: string;
  title: string;
  date: string;
  status: 'confirmed' | 'signal';
  includedInExport: boolean;
  content: string;
}

export default function ViewerScreen() {
  const [activeTab, setActiveTab] = React.useState<'overview' | 'entity' | 'reports'>('overview');
  const [searchQuery, setSearchQuery] = React.useState('');
  const [selectedSource] = React.useState('all');
  const [isSimulateOpen, setIsSimulateOpen] = React.useState(false);
  const [graphData, setGraphData] = React.useState<{ nodes: GraphNode[]; links: GraphLink[] }>({ nodes: [], links: [] });
  const [selectedEntity, setSelectedEntity] = React.useState<{ id: string; name: string; type: string; canonicalId: string } | null>(null);
  const [entityDetails, setEntityDetails] = React.useState<{ entity: any, facts: EntityFact[], incoming: EntityFact[] } | null>(null);
  const [searchResults, setSearchResults] = React.useState<import('../api/client').EntitySearchResult[]>([]);
  const [isSearching, setIsSearching] = React.useState(false);
  const [showSearchDropdown, setShowSearchDropdown] = React.useState(false);
  const [reports, setReports] = React.useState<CommunityReportItem[]>([]);
  const [selectedReport, setSelectedReport] = React.useState<CommunityReportItem | null>(null);
  const [simulateQuery, setSimulateQuery] = React.useState('');
  const [simulateAccess, setSimulateAccess] = React.useState('internal');
  const [simulation, setSimulation] = React.useState<SimulationResult | null>(null);
  const [isSimulating, setIsSimulating] = React.useState(false);
  const [showSkills, setShowSkills] = React.useState(false);
  const [showLabels, setShowLabels] = React.useState(true);
  const [hoveredNodeId, setHoveredNodeId] = React.useState<string | null>(null);
  const [hoveredLink, setHoveredLink] = React.useState<GraphLink | null>(null);

  const { openProvenance } = useProvenance();
  const graphContainerRef = React.useRef<HTMLDivElement>(null);
  const graphRef = React.useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(undefined);
  const [graphDim, setGraphDim] = React.useState({ width: 800, height: 640 });
  const didFitRef = React.useRef(false);

  const [scopesList, setScopesList] = React.useState<import('../api/client').Scope[]>([]);
  const [selectedScopeVersion, setSelectedScopeVersion] = React.useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('scope_version_id') || 'all';
  });
  const [isLoadingGraph, setIsLoadingGraph] = React.useState(true);
  const [isGraphReady, setIsGraphReady] = React.useState(false);

  // Whether to show the loading overlay: hide only when both data loaded AND simulation settled
  const showLoadingOverlay = isLoadingGraph || !isGraphReady;

  // Fetch scopes list for dropdown selector
  React.useEffect(() => {
    api.scopes.list()
      .then(res => setScopesList(res || []))
      .catch(err => console.error("Failed to fetch scopes:", err));
  }, []);

  // Fetch graph topology whenever selectedScopeVersion changes
  React.useEffect(() => {
    setIsLoadingGraph(true);
    setIsGraphReady(false);
    didFitRef.current = false;
    const versionParam = selectedScopeVersion === 'all' ? undefined : selectedScopeVersion;
    api.graph.topology(versionParam)
      .then(data => {
        if (data?.nodes && data?.links) {
          setGraphData(data);
        }
      })
      .catch(err => console.error("Failed to fetch topology:", err))
      .finally(() => setIsLoadingGraph(false));
  }, [selectedScopeVersion]);

  // Fetch entity details when selected
  React.useEffect(() => {
    if (selectedEntity?.canonicalId) {
      setEntityDetails(null); // Clear old details while loading
      api.graph.entity(selectedEntity.canonicalId)
        .then(data => {
          if (data.entity) {
            const mapFact = (f: any): EntityFact => ({
              id: f.fact_id,
              predicate: f.relation_type,
              value: f.target_name || f.target_canonical_id || f.source_name || f.source_canonical_id,
              provenance: {
                sourceChunkText: f.source_chunk_text || f.fact || '',
                sourceDocumentName: f.source_document_name || 'Unknown document',
                accessTag: f.access_tag || 'unknown',
                extractionConfidence: f.confidence ?? 1.0,
                resolutionConfidence: f.resolution_confidence ?? 1.0,
                mutationId: f.mutation_id || 'unknown',
                snapshotVersion: f.snapshot_version || 'unreleased'
              }
            });
            
            setEntityDetails({
              entity: data.entity,
              facts: (data.facts || []).map((f: any) => ({
                ...mapFact(f),
                linkedEntityId: f.target_canonical_id
              })),
              incoming: (data.incoming || []).map((f: any) => ({
                ...mapFact(f),
                value: f.source_name || f.source_canonical_id || 'Unknown Source',
                linkedEntityId: f.source_canonical_id
              }))
            });
          }
        })
        .catch(err => console.error("Failed to fetch entity details:", err));
    }
  }, [selectedEntity]);

  // Debounced search
  React.useEffect(() => {
    const q = searchQuery.trim();
    if (q.length < 2) {
      setSearchResults([]);
      setShowSearchDropdown(false);
      return;
    }
    const timer = setTimeout(() => {
      setIsSearching(true);
      api.graph.search(q)
        .then(res => {
          setSearchResults(res.results || []);
          setShowSearchDropdown(true);
        })
        .finally(() => setIsSearching(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  React.useEffect(() => {
    api.reports.list()
      .then(setReports)
      .catch(err => console.error('Failed to fetch reports:', err));
  }, []);

  const runSimulation = async () => {
    if (!simulateQuery.trim()) return;
    setIsSimulating(true);
    try {
      setSimulation(await api.simulate({ query: simulateQuery, access_level: simulateAccess }));
    } catch (err) {
      console.error('Simulation failed:', err);
      setSimulation(null);
    } finally {
      setIsSimulating(false);
    }
  };

  React.useEffect(() => {
    if (graphContainerRef.current) {
      setGraphDim({
        width: graphContainerRef.current.clientWidth,
        height: 680
      });
    }
  }, [activeTab, graphData.nodes.length]);

  const visibleGraph = React.useMemo(
    () => filterGraph(graphData, { showSkills, search: searchQuery, source: selectedSource }),
    [graphData, showSkills, searchQuery, selectedSource, activeTab]
  );



  // Spread the layout: resume graphs are hub-and-spoke and collapse into a blob
  // under the default charge/link settings.
  React.useEffect(() => {
    const fg = graphRef.current;
    if (!fg || visibleGraph.nodes.length === 0) return;

    didFitRef.current = false;
    const hub =
      visibleGraph.nodes.find((n) => n.type === 'Employee') ||
      [...visibleGraph.nodes].sort((a, b) => b.val - a.val)[0];

    const charge = fg.d3Force('charge') as
      | { strength?: (n: number) => unknown; distanceMax?: (n: number) => unknown }
      | undefined;
    charge?.strength?.(-900);
    charge?.distanceMax?.(1200);

    const link = fg.d3Force('link') as
      | {
          distance?: (fn: (l: GraphLink) => number) => unknown;
          strength?: (n: number) => unknown;
        }
      | undefined;
    link?.distance?.(() => 260);
    link?.strength?.(0.12);

    fg.d3Force(
      'collide',
      forceCollide(() => 56).strength(1).iterations(4)
    );
    // Pin the person near the origin and ring everyone else around them.
    if (hub) {
      fg.d3Force(
        'radial',
        forceRadial((node: GraphNode) => (node.id === hub.id ? 0 : 280), 0, 0).strength(0.45)
      );
    }
    fg.d3ReheatSimulation();
  }, [visibleGraph, graphDim.width]);

  const hoveredRelations = React.useMemo(() => {
    if (!hoveredNodeId) return [];
    const names = new Map(visibleGraph.nodes.map((n) => [n.id, n.name]));
    return visibleGraph.links
      .map((l) => {
        const s = nodeId(l.source);
        const t = nodeId(l.target);
        if (s !== hoveredNodeId && t !== hoveredNodeId) return null;
        const rel = (l.relation_type || 'RELATED').replace(/_/g, ' ');
        if (s === hoveredNodeId) {
          return { dir: 'out' as const, rel, other: names.get(t) || t };
        }
        return { dir: 'in' as const, rel, other: names.get(s) || s };
      })
      .filter(Boolean) as { dir: 'in' | 'out'; rel: string; other: string }[];
  }, [hoveredNodeId, visibleGraph]);

  const paintNode = React.useCallback(
    (node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const size = Math.max(8, Math.sqrt(node.val || 1) * 2.5);
      const color = getNodeColor(node);
      const needsAttn = node.attention?.required || node.need_attention;
      const hovered = hoveredNodeId === node.id || 
        (hoveredLink != null && (nodeId(hoveredLink.source) === node.id || nodeId(hoveredLink.target) === node.id));
      const connected =
        (hoveredNodeId != null &&
          visibleGraph.links.some((l) => {
            const s = nodeId(l.source);
            const t = nodeId(l.target);
            return (
              (s === hoveredNodeId && t === node.id) ||
              (t === hoveredNodeId && s === node.id)
            );
          })) ||
        (hoveredLink != null &&
          (nodeId(hoveredLink.source) === node.id || nodeId(hoveredLink.target) === node.id));
      const dimmed = (hoveredNodeId != null || hoveredLink != null) && !hovered && !connected;

      ctx.globalAlpha = dimmed ? 0.22 : 1;
      ctx.beginPath();
      ctx.arc(node.x || 0, node.y || 0, size, 0, 2 * Math.PI, false);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = needsAttn
        ? '#f97316' 
        : (hovered ? '#38bdf8' : (color === '#18181b' ? 'rgba(255,255,255,0.95)' : 'rgba(15,23,42,0.15)'));
      ctx.lineWidth = (needsAttn ? 3 : (hovered ? 2.5 : 1.5)) / globalScale;
      ctx.stroke();

      if (showLabels || hovered) {
        const fontSize = Math.max(10, 11 / globalScale);
        ctx.font = `600 ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        const labelY = (node.y || 0) + size + 5;
        const text = node.name.length > 34 ? `${node.name.slice(0, 32)}…` : node.name;
        const padX = 4;
        const metrics = ctx.measureText(text);
        ctx.fillStyle = 'rgba(252,252,251,0.94)';
        ctx.fillRect(
          (node.x || 0) - metrics.width / 2 - padX,
          labelY - 1,
          metrics.width + padX * 2,
          fontSize + 4
        );
        ctx.fillStyle = '#0f172a';
        ctx.fillText(text, node.x || 0, labelY);
      }
      ctx.globalAlpha = 1;
    },
    [hoveredNodeId, hoveredLink, showLabels, visibleGraph.links]
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-display text-ink tracking-tight">Knowledge Graph Viewer</h1>
        <div className="flex items-center gap-4 z-50">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-ink-muted">Scope:</span>
            <select 
              className="bg-white border border-hairline rounded-md px-3 py-1.5 text-body text-ink focus:outline-none focus:ring-2 focus:ring-ledger"
              value={selectedScopeVersion}
              onChange={e => {
                const val = e.target.value;
                setSelectedScopeVersion(val);
                const url = new URL(window.location.href);
                if (val === 'all') {
                  url.searchParams.delete('scope_version_id');
                } else {
                  url.searchParams.set('scope_version_id', val);
                }
                window.history.replaceState({}, '', url.toString());
              }}
            >
              <option value="all">All Assigned Scopes</option>
              {scopesList.filter(s => s.active_version_id).map(s => (
                <option key={s.id} value={s.active_version_id!}>{s.name}</option>
              ))}
            </select>
          </div>



          <div className="relative w-64">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-ink-muted" />
            <input 
              type="text" 
              placeholder="Global search..." 
              className="w-full pl-9 pr-4 py-2 border border-hairline rounded-md text-body focus:outline-none focus:ring-2 focus:ring-ledger bg-white"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => setShowSearchDropdown(true)}
              onBlur={() => setTimeout(() => setShowSearchDropdown(false), 200)}
            />
            {showSearchDropdown && (searchQuery.length > 1) && (
              <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-hairline rounded-md shadow-lg overflow-hidden">
                {isSearching ? (
                  <div className="p-3 text-caption text-ink-muted italic">Searching database...</div>
                ) : searchResults.length > 0 ? (
                  <ul className="max-h-60 overflow-y-auto divide-y divide-hairline">
                    {searchResults.map(res => (
                      <li 
                        key={res.id} 
                        className="px-3 py-2 hover:bg-paper/80 cursor-pointer"
                        onClick={() => {
                          setSelectedEntity({ id: res.id, canonicalId: res.id, name: res.name, type: res.type });
                          setSearchQuery('');
                          setShowSearchDropdown(false);
                          setActiveTab('entity');
                        }}
                      >
                        <div className="font-medium text-ink text-body truncate">{res.name}</div>
                        <div className="text-caption text-ink-muted">{res.type}</div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="p-3 text-caption text-ink-muted italic">No matching entities found.</div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="flex border-b border-hairline">
        {(['overview', 'entity', 'reports'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 font-medium text-body capitalize border-b-2 transition-colors ${
              activeTab === tab 
                ? 'border-ledger text-ledger' 
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab === 'overview' ? 'Tenant Overview' : tab === 'entity' ? 'Entity Detail' : 'Community Reports'}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <div className="border border-hairline rounded-lg bg-white overflow-hidden relative">
          <div className="p-4 border-b border-hairline bg-paper/50 flex justify-between items-center gap-4 flex-wrap">
            <div className="space-y-1">
              <span className="font-medium text-ink flex items-center">
                <Info className="mr-2 h-4 w-4 text-ink-muted" />
                Hover a node to see its relations below. Click for full facts.
              </span>
              <div className="text-caption text-ink-muted pl-6">
                {visibleGraph.nodes.length} nodes · {visibleGraph.links.length} relations
                {!showSkills && visibleGraph.hiddenSkills > 0
                  ? ` · ${visibleGraph.hiddenSkills} skills hidden`
                  : ''}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-caption text-ink cursor-pointer">
                <input
                  type="checkbox"
                  className="rounded text-ledger focus:ring-ledger"
                  checked={showSkills}
                  onChange={(e) => setShowSkills(e.target.checked)}
                />
                Show skills / tech
              </label>
              <label className="flex items-center gap-2 text-caption text-ink cursor-pointer">
                <input
                  type="checkbox"
                  className="rounded text-ledger focus:ring-ledger"
                  checked={showLabels}
                  onChange={(e) => setShowLabels(e.target.checked)}
                />
                Show node names
              </label>
              <div className="flex flex-wrap gap-3 text-caption text-ink-muted">
                {Object.entries(SOURCE_COLORS).filter(([type]) => type !== 'default').map(([type, color]) => (
                  <span key={type} className="inline-flex items-center gap-1.5 capitalize font-medium">
                    <span className="inline-block h-2.5 w-2.5 rounded-full border border-black/10 shadow-sm" style={{ backgroundColor: color }} />
                    {type}
                  </span>
                ))}
              </div>
            </div>
          </div>
          <div ref={graphContainerRef} className="relative w-full bg-[#fcfcfb] min-h-[680px]">

            {/* ── Full-canvas loading overlay ──────────────────────────────── */}
            <div
              className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-6 bg-[#fcfcfb] select-none"
              style={{
                transition: 'opacity 0.5s ease, visibility 0.5s ease',
                opacity: showLoadingOverlay ? 1 : 0,
                visibility: showLoadingOverlay ? 'visible' : 'hidden',
                pointerEvents: showLoadingOverlay ? 'all' : 'none',
              }}
            >
              {/* Animated network visual */}
              <div className="relative w-36 h-36">
                {/* Expanding ring */}
                <span
                  className="absolute inset-0 rounded-full border-2 border-ledger/30"
                  style={{ animation: 'graph-ring-expand 2s ease-out infinite' }}
                />
                <span
                  className="absolute inset-0 rounded-full border border-ledger/15"
                  style={{ animation: 'graph-ring-expand 2s ease-out infinite', animationDelay: '0.7s' }}
                />
                {/* 6 orbiting dots */}
                {[0,1,2,3,4,5].map(i => (
                  <span
                    key={i}
                    className="graph-orbit-dot"
                    style={{
                      animationDelay: `${-i * (1.8 / 6)}s`,
                      animationDuration: '1.8s',
                      opacity: 1 - i * 0.1,
                    }}
                  />
                ))}
                {/* SVG spokes */}
                <svg className="absolute inset-0 w-full h-full opacity-10 pointer-events-none" viewBox="0 0 144 144">
                  {[0,60,120,180,240,300].map((deg, i) => {
                    const rad = (deg * Math.PI) / 180;
                    return <line key={i} x1="72" y1="72"
                      x2={72 + 52 * Math.cos(rad)}
                      y2={72 + 52 * Math.sin(rad)}
                      stroke="#2B4570" strokeWidth="1" strokeDasharray="3 3"
                    />;
                  })}
                </svg>
                {/* Hub */}
                <span className="absolute inset-0 m-auto w-8 h-8 rounded-full bg-ledger/15 border-2 border-ledger animate-pulse" />
              </div>

              {/* Labels */}
              <div className="text-center">
                <p className="text-base font-semibold text-ink">
                  {isLoadingGraph ? 'Fetching graph data…' : 'Settling layout…'}
                </p>
                <p className="text-sm text-ink-muted mt-1">
                  {selectedScopeVersion === 'all' ? 'Merging all assigned scopes' : 'Filtering to selected scope'}
                </p>
                {/* Node/edge count badge — shown once data is available */}
                {!isLoadingGraph && graphData.nodes.length > 0 && (
                  <p className="mt-3 inline-block bg-ledger/10 text-ledger text-xs font-medium px-3 py-1 rounded-full">
                    {graphData.nodes.length} nodes · {graphData.links.length} edges
                  </p>
                )}
              </div>

              {/* Progress bar */}
              <div className="w-48 h-1 bg-hairline rounded-full overflow-hidden">
                <div
                  className="h-full bg-ledger rounded-full"
                  style={{
                    width: isLoadingGraph ? '40%' : '80%',
                    transition: 'width 0.6s ease',
                    animation: isLoadingGraph ? 'none' : 'graph-progress-pulse 1s ease-in-out infinite alternate',
                  }}
                />
              </div>
            </div>

            {/* ── ForceGraph2D — always mounted so simulation can run ──────── */}
            {visibleGraph.nodes.length > 0 && typeof window !== 'undefined' ? (
               <ForceGraph2D
                 ref={graphRef}
                 width={graphDim.width}
                 height={graphDim.height}
                 graphData={visibleGraph}
                 nodeLabel={(node: GraphNode) => {
                    let label = `<strong>${node.name}</strong>${node.type ? ` · <em>${node.type}</em>` : ''}`;
                    if (node.attention?.required || node.need_attention) {
                      label = `<div style="color: #ef4444; font-weight: bold; margin-bottom: 4px;">⚠️ NEEDS ATTENTION</div>${label}`;
                    }
                    if (node.semantic?.summary || node.summary) {
                      label = `${label}<div style="margin-top: 6px; font-size: 11px; color: #cbd5e1; max-width: 260px; white-space: normal;">${node.semantic?.summary || node.summary}</div>`;
                    }
                    return `<div style="padding: 6px; font-family: sans-serif;">${label}</div>`;
                  }}
                 linkLabel={(link: GraphLink) => {
                   const sName = typeof link.source === 'object' ? (link.source as any).name : link.source;
                   const tName = typeof link.target === 'object' ? (link.target as any).name : link.target;
                   const rel = (link.relation_type || '').replace(/_/g, ' ');
                   return `${sName} —[${rel}]→ ${tName}`;
                 }}
                 nodeCanvasObject={paintNode}
                 nodePointerAreaPaint={(node: GraphNode, color, ctx) => {
                   const size = Math.max(8, Math.sqrt(node.val || 1) * 2.5) + 6;
                   ctx.fillStyle = color;
                   ctx.beginPath();
                   ctx.arc(node.x || 0, node.y || 0, size, 0, 2 * Math.PI, false);
                   ctx.fill();
                 }}
                 linkPointerAreaPaint={(link: any, color, ctx) => {
                   ctx.strokeStyle = color;
                   ctx.lineWidth = 10;
                   ctx.beginPath();
                   ctx.moveTo(link.source.x, link.source.y);
                   ctx.lineTo(link.target.x, link.target.y);
                   ctx.stroke();
                 }}
                 linkColor={(link: GraphLink) => {
                   const s = nodeId(link.source);
                   const t = nodeId(link.target);
                   const isLinkHovered = hoveredLink &&
                     nodeId(hoveredLink.source) === s &&
                     nodeId(hoveredLink.target) === t;
                   const active = isLinkHovered ||
                     (hoveredNodeId != null && (s === hoveredNodeId || t === hoveredNodeId));
                   if (hoveredLink && !isLinkHovered) return '#f1f5f9';
                   if (hoveredNodeId && !active) return '#f1f5f9';
                   if ((link.relation_type || '') === 'USES') return active ? '#0d9488' : '#94a3b8';
                   return active ? '#0f766e' : '#475569';
                 }}
                 linkWidth={(link: GraphLink) => {
                   const s = nodeId(link.source);
                   const t = nodeId(link.target);
                   const isLinkHovered = hoveredLink &&
                     nodeId(hoveredLink.source) === s &&
                     nodeId(hoveredLink.target) === t;
                   const active = isLinkHovered ||
                     (hoveredNodeId != null && (s === hoveredNodeId || t === hoveredNodeId));
                   if (active) return 3.5;
                   return (link.relation_type || '') === 'USES' ? 1.2 : 2.2;
                 }}
                 linkDirectionalArrowLength={6}
                 linkDirectionalArrowRelPos={0.95}
                 linkCurvature={0}
                 d3AlphaDecay={0.022}
                 d3VelocityDecay={0.3}
                 cooldownTicks={200}
                 backgroundColor="transparent"
                 onNodeHover={(node: GraphNode | null) => setHoveredNodeId(node?.id ?? null)}
                 onLinkHover={(link: GraphLink | null) => setHoveredLink(link)}
                 onEngineStop={() => {
                   if (!didFitRef.current && graphRef.current) {
                     didFitRef.current = true;
                     graphRef.current.zoomToFit(400, 80);
                   }
                   setIsGraphReady(true);
                 }}
                 onNodeClick={(node: GraphNode) => {
                   setSelectedEntity({
                     id: node.id,
                     name: node.name,
                     type: node.type || 'Entity',
                     canonicalId: node.id
                   });
                   setActiveTab('entity');
                 }}
               />
            ) : !showLoadingOverlay && (
               <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-ink-muted">
                 <svg className="w-10 h-10 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                   <circle cx="12" cy="12" r="10" strokeWidth="1.5" />
                   <path strokeLinecap="round" strokeWidth="1.5" d="M8 12h8M12 8v8" />
                 </svg>
                 <p className="italic text-body">
                   {graphData.nodes.length === 0
                     ? 'No graph nodes available. Ensure a sync has completed.'
                     : 'No nodes match this search or filter.'}
                 </p>
               </div>
            )}
          </div>
          {hoveredLink != null && (
            <div className="border-t border-hairline bg-white px-4 py-3 max-h-40 overflow-y-auto">
              <div className="text-caption uppercase tracking-wider text-ink-muted mb-1">
                Relation Details
              </div>
              {(() => {
                const sName = typeof hoveredLink.source === 'object' ? (hoveredLink.source as any).name : hoveredLink.source;
                const tName = typeof hoveredLink.target === 'object' ? (hoveredLink.target as any).name : hoveredLink.target;
                const rel = (hoveredLink.relation_type || '').replace(/_/g, ' ');
                return (
                  <div className="text-body text-ink flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-semibold text-ledger">{sName}</span>
                    <span className="font-mono text-caption text-ink bg-paper border border-hairline rounded px-1.5 py-0.5">
                      {rel}
                    </span>
                    <span className="font-semibold text-ledger">{tName}</span>
                  </div>
                );
              })()}
            </div>
          )}
          {hoveredLink == null && hoveredNodeId != null && (
            <div className="border-t border-hairline bg-white px-4 py-3 max-h-40 overflow-y-auto">
              {(() => {
                const hoveredNode = visibleGraph.nodes.find((n) => n.id === hoveredNodeId);
                if (!hoveredNode) return null;
                return (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <div className="font-semibold text-body text-ink flex items-center gap-2">
                        <span>{hoveredNode.name}</span>
                        <span className="text-caption font-normal text-ink-muted bg-paper px-1.5 py-0.5 rounded border border-hairline">
                          {hoveredNode.type || 'Entity'}
                        </span>
                      </div>
                      <div className="text-caption text-ink-muted">
                        Connections: {hoveredNode.val - 1}
                      </div>
                    </div>
                    {hoveredRelations.length > 0 ? (
                      <ul className="space-y-1.5 mt-2">
                        {hoveredRelations.map((r, i) => (
                          <li key={`${r.rel}-${r.other}-${i}`} className="text-body text-ink flex flex-wrap gap-x-2 gap-y-0.5">
                            <span className="font-mono text-caption text-ledger bg-paper border border-hairline rounded px-1.5 py-0.5">
                              {r.rel}
                            </span>
                            <span className="text-ink-muted">{r.dir === 'out' ? '→' : '←'}</span>
                            <span className="font-medium">{r.other}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="text-caption text-ink-muted italic mt-1">
                        No active relations detected for this entity.
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      )}

        {activeTab === 'entity' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            {selectedEntity ? (
              <div className="p-6 border border-hairline rounded-lg bg-white">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h2 className="text-display text-ink mb-2 break-all">{selectedEntity.name}</h2>
                    <div className="flex items-center gap-3">
                      <Badge status="confirmed">{selectedEntity.type || 'Entity'}</Badge>
                      <span className="font-mono text-caption text-ink bg-paper px-2 py-1 rounded border border-hairline flex items-center">
                        canonical_id: {selectedEntity.canonicalId} <Copy className="ml-2 h-3 w-3 cursor-pointer text-ink-muted hover:text-ink" />
                      </span>
                    </div>
                  </div>
                </div>
                
                { (entityDetails?.entity?.attention?.required || entityDetails?.entity?.need_attention) && (
                  <div className="mt-4 p-4 bg-red-50 border border-red-200 text-red-700 rounded-md flex items-center gap-2">
                    <span className="font-semibold text-body">⚠️ Needs Attention</span>
                    <span className="text-caption">This entity has active bugs, build failures, or review flags associated with it.</span>
                  </div>
                )}
                
                { (entityDetails?.entity?.semantic?.summary || entityDetails?.entity?.summary) && (
                  <div className="mt-4 p-4 bg-paper/50 rounded-md border border-hairline space-y-1.5">
                    <span className="text-caption font-semibold text-ink-muted uppercase tracking-wider">File Summary</span>
                    <p className="text-body text-ink leading-relaxed whitespace-pre-wrap break-words">
                      {entityDetails.entity.semantic?.summary || entityDetails.entity.summary}
                    </p>
                  </div>
                )}
                
                {!entityDetails && (
                  <div className="mt-8 text-center text-ink-muted italic text-body py-8">
                    Loading entity details...
                  </div>
                )}
                
                {entityDetails && (
                  <div className="mt-8 space-y-8">
                    {/* Outgoing Facts */}
                    <div>
                      <h3 className="text-h2 text-ink mb-4">Outgoing Relationships (Facts)</h3>
                      <table className="w-full table-fixed text-left text-body border border-hairline rounded-md overflow-hidden">
                         <thead className="bg-paper border-b border-hairline text-ink-muted text-caption uppercase tracking-wider">
                          <tr>
                            <th className="px-4 py-2 font-medium w-auto">Target</th>
                            <th className="px-4 py-2 font-medium w-56">Relation</th>
                            <th className="px-4 py-2 font-medium w-32">Provenance</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-hairline">
                          {entityDetails.facts.map(fact => (
                            <tr key={fact.id} className="hover:bg-paper/50 transition-colors">
                              <td className="px-4 py-3 text-ink font-medium break-all">
                                {fact.linkedEntityId ? (
                                  <button 
                                    onClick={() => setSelectedEntity({ id: fact.linkedEntityId!, canonicalId: fact.linkedEntityId!, name: fact.value, type: 'Entity' })}
                                    className="text-ink hover:underline text-left font-medium break-all"
                                  >
                                    {fact.value}
                                  </button>
                                ) : (
                                  fact.value
                                )}
                              </td>
                              <td className="px-4 py-3 font-medium text-ink"><span className="bg-paper border border-hairline px-1.5 py-0.5 rounded font-mono text-caption break-all inline-block">{fact.predicate}</span></td>
                              <td className="px-4 py-3">
                                <button 
                                  onClick={() => openProvenance(fact.provenance)}
                                  className="text-ledger hover:underline text-caption font-medium flex items-center"
                                >
                                  View Thread
                                </button>
                              </td>
                            </tr>
                          ))}
                          {entityDetails.facts.length === 0 && (
                            <tr>
                              <td colSpan={3} className="px-4 py-6 text-center text-ink-muted italic">
                                No outgoing facts recorded.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>

                    {/* Incoming Facts */}
                    <div>
                      <h3 className="text-h2 text-ink mb-4">Incoming Relationships</h3>
                      <table className="w-full table-fixed text-left text-body border border-hairline rounded-md overflow-hidden">
                         <thead className="bg-paper border-b border-hairline text-ink-muted text-caption uppercase tracking-wider">
                          <tr>
                            <th className="px-4 py-2 font-medium w-auto">Source</th>
                            <th className="px-4 py-2 font-medium w-56">Relation</th>
                            <th className="px-4 py-2 font-medium w-32">Provenance</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-hairline">
                          {entityDetails.incoming.map(fact => (
                            <tr key={fact.id} className="hover:bg-paper/50 transition-colors">
                              <td className="px-4 py-3 text-ink font-medium break-all">
                                {fact.linkedEntityId ? (
                                  <button 
                                    onClick={() => setSelectedEntity({ id: fact.linkedEntityId!, canonicalId: fact.linkedEntityId!, name: fact.value || 'Unknown Source', type: 'Entity' })}
                                    className="text-ink hover:underline text-left font-medium break-all"
                                  >
                                    {fact.value || 'Unknown Source'}
                                  </button>
                                ) : (
                                  fact.value || 'Unknown Source'
                                )}
                              </td>
                              <td className="px-4 py-3 font-medium text-ink"><span className="bg-paper border border-hairline px-1.5 py-0.5 rounded font-mono text-caption break-all inline-block">{fact.predicate}</span></td>
                              <td className="px-4 py-3">
                                <button 
                                  onClick={() => openProvenance(fact.provenance)}
                                  className="text-ledger hover:underline text-caption font-medium flex items-center"
                                >
                                  View Thread
                                </button>
                              </td>
                            </tr>
                          ))}
                          {entityDetails.incoming.length === 0 && (
                            <tr>
                              <td colSpan={3} className="px-4 py-6 text-center text-ink-muted italic">
                                No incoming relationships detected.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="p-12 text-center text-ink-muted border border-hairline bg-white rounded-lg italic">
                Use global search above or click a graph node to view entity details.
              </div>
            )}
          </div>
          
          <div className="lg:col-span-1 space-y-6">
            <div className="p-4 border border-hairline rounded-lg bg-white">
              <h3 className="text-h2 text-ink mb-4">Merge History</h3>
              <div className="text-ink-muted text-caption italic">No merge history for entity.</div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'reports' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1 border border-hairline bg-white rounded-lg overflow-hidden flex flex-col h-[600px]">
             <div className="p-4 border-b border-hairline bg-paper/50">
               <input 
                 type="text" 
                 placeholder="Search reports..." 
                 className="w-full px-3 py-2 border border-hairline rounded-md text-body focus:outline-none focus:border-ledger"
               />
             </div>
             <div className="flex-1 overflow-y-auto divide-y divide-hairline">
                {reports.map(report => (
                  <div 
                    key={report.id} 
                    onClick={() => setSelectedReport(report)}
                    className={`p-4 cursor-pointer transition-colors ${selectedReport?.id === report.id ? 'bg-paper/50 border-l-2 border-ledger' : 'hover:bg-paper/30'}`}
                  >
                    <div className="font-medium text-ink mb-1">{report.title}</div>
                    <div className="text-caption text-ink-muted flex justify-between">
                      <span>{report.date}</span>
                      <Badge status={report.status}>{report.includedInExport ? 'Exported' : 'Draft'}</Badge>
                    </div>
                  </div>
                ))}
                {reports.length === 0 && (
                  <div className="p-8 text-center text-ink-muted italic">No community reports found.</div>
                )}
             </div>
          </div>
          <div className="lg:col-span-2 border border-hairline bg-white rounded-lg p-8 flex flex-col h-[600px] overflow-y-auto">
             {selectedReport ? (
               <>
                 <div className="flex justify-between items-start mb-6">
                   <h2 className="text-display text-ink">{selectedReport.title}</h2>
                   <div className="flex gap-2">
                     <Button variant="ghost" size="sm"><FileEdit className="mr-2 h-4 w-4"/>Edit</Button>
                     <Button variant="secondary" size="sm">Regenerate</Button>
                   </div>
                 </div>
                 
                 <div className="flex items-center gap-3 mb-8 bg-paper p-3 rounded-md border border-hairline w-fit">
                   <span className="text-body font-medium text-ink">Include in export</span>
                   <ToggleRight className="h-6 w-6 text-confirmed cursor-pointer" />
                 </div>

                 <div className="prose prose-p:font-serif prose-p:text-narrative prose-p:text-ink max-w-none">
                   <p>{selectedReport.content}</p>
                 </div>
               </>
             ) : (
               <div className="h-full flex items-center justify-center text-ink-muted italic">
                 Select a report from the browser list to view narrative.
               </div>
             )}
          </div>
        </div>
      )}

      {/* Simulate Agent Query Panel */}
      <div className="mt-12 border border-hairline rounded-lg bg-white overflow-hidden shadow-sm">
        <button 
          onClick={() => setIsSimulateOpen(!isSimulateOpen)}
          className="w-full p-4 flex justify-between items-center bg-paper hover:bg-paper/80 transition-colors"
        >
          <div className="flex items-center gap-2">
            <Terminal className="h-5 w-5 text-ledger" />
            <span className="font-medium text-ink">Simulate Agent Query</span>
          </div>
          <span className="text-caption text-ink-muted">Internal QA — not part of the exported product</span>
        </button>
        
        {isSimulateOpen && (
          <div className="p-6 border-t border-hairline space-y-4">
            <div className="flex gap-4">
              <input 
                type="text" 
                placeholder="Type a natural language query to trace assembly..."
                className="flex-1 px-4 py-2 border border-hairline rounded-md text-body focus:outline-none focus:border-ledger"
                value={simulateQuery}
                onChange={e => setSimulateQuery(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') runSimulation(); }}
              />
              <select
                className="px-3 py-2 border border-hairline rounded-md text-body focus:outline-none focus:border-ledger"
                value={simulateAccess}
                onChange={e => setSimulateAccess(e.target.value)}
                title="The clearance the calling agent would have"
              >
                <option value="public">public</option>
                <option value="internal">internal</option>
                <option value="confidential">confidential</option>
                <option value="restricted">restricted</option>
              </select>
              <Button variant="primary" onClick={runSimulation} disabled={isSimulating}>
                {isSimulating ? 'Running...' : 'Simulate'}
              </Button>
            </div>

            {simulation && simulation.withheld_count > 0 && (
              <div className="border border-alert/40 bg-alert/5 text-alert rounded-md px-4 py-3 text-body">
                {simulation.withheld_count} fact(s) were withheld from this agent by access control.
              </div>
            )}

            <div className="bg-ink text-paper p-4 rounded-md font-mono text-caption overflow-auto max-h-[300px]">
<pre>{simulation ? JSON.stringify({
  query: simulation.query,
  access_level: simulation.access_level,
  resolved_entities: simulation.resolved_entities,
  assembled_context: simulation.context,
  retrieved_facts: simulation.retrieved_facts.map(f => ({
    fact: f.fact,
    score: f.score,
    valid_at: f.valid_at,
    provenance: f.provenance,
  })),
  withheld_facts: simulation.withheld_facts,
}, null, 2) : `{
  "query": "",
  "resolved_entities": [],
  "assembled_context": {
    "facts": [],
    "reports": []
  }
}`}</pre>
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
