import * as React from 'react';
import { Activity, AlertTriangle, ServerCrash, RefreshCw } from 'lucide-react';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { api, type ExportMetrics, type LlmTelemetryData } from '../api/client';

const Sparkline = ({ data, color, height = 40 }: { data: number[], color: string, height?: number }) => {
  if (!data || data.length === 0) return <div className="h-full flex items-center justify-center text-caption text-ink-muted italic">No trend data</div>;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const width = 100;
  
  const points = data.map((d, i) => {
    const x = (i / (data.length - 1 || 1)) * width;
    const y = height - ((d - min) / range) * height;
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full overflow-visible" preserveAspectRatio="none">
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  );
}

const TrendCard = ({ title, data = [], colorClass, value = '-', unit = '', label = '' }: any) => (
  <div className="border border-hairline rounded-lg bg-white p-4 flex flex-col justify-between">
    <div className="text-caption text-ink-muted uppercase font-medium tracking-wider mb-4">{title}</div>
    <div className="flex items-end justify-between mb-4">
      <div className="text-h1 text-ink">{value}<span className="text-body text-ink-muted ml-1">{unit}</span></div>
      <div className="text-caption text-ink-muted">{label}</div>
    </div>
    <div className="h-12 w-full mt-auto">
      <Sparkline data={data} color={colorClass} height={48} />
    </div>
  </div>
);

export interface StageInfo {
  name: string;
  throughput: string;
  errorRate: string;
  status: 'confirmed' | 'signal' | 'alert' | 'neutral';
}

export interface MismatchItem {
  id: string;
  txId: string;
  store: string;
  durationMinutes: number;
}

export default function ObservabilityScreen() {
  const [activeTab, setActiveTab] = React.useState<'pipeline' | 'consistency' | 'quality' | 'export' | 'llm'>('pipeline');
  const [activeStage, setActiveStage] = React.useState<string>('Idle');
  
  const [funnelStages, setFunnelStages] = React.useState<StageInfo[]>([
    { name: 'Ingestion', throughput: '-', errorRate: '0.0%', status: 'neutral' },
    { name: 'Chunking', throughput: '-', errorRate: '0.0%', status: 'neutral' },
    { name: 'Extraction', throughput: '-', errorRate: '0.0%', status: 'neutral' },
    { name: 'Resolution', throughput: '-', errorRate: '0.0%', status: 'neutral' },
    { name: 'Consolidation', throughput: '-', errorRate: '0.0%', status: 'neutral' },
    { name: 'Community Detection', throughput: '-', errorRate: '0.0%', status: 'neutral' },
    { name: 'Export', throughput: '-', errorRate: '0.0%', status: 'neutral' },
  ]);

  const [mismatches, setMismatches] = React.useState<MismatchItem[]>([]);
  const [qualityMetrics, setQualityMetrics] = React.useState<any>({});
  const [isRefreshing, setIsRefreshing] = React.useState(false);
  const [exportMetrics, setExportMetrics] = React.useState<ExportMetrics | null>(null);
  const [llmTelemetry, setLlmTelemetry] = React.useState<LlmTelemetryData | null>(null);

  const fetchLlmTelemetry = async () => {
    try {
      setLlmTelemetry(await api.observability.llmTelemetry());
    } catch (e) {
      console.error('Failed to fetch LLM telemetry', e);
    }
  };

  const fetchExportMetrics = async () => {
    try {
      setExportMetrics(await api.observability.exportMetrics());
    } catch (e) {
      console.error('Failed to fetch export metrics', e);
    }
  };

  const escalate = async (id: string) => {
    // Removed locally too: an acknowledged mismatch stops being reported, so
    // leaving the row visible would suggest the click did nothing.
    setMismatches(current => current.filter(m => m.id !== id));
    try {
      await api.observability.escalate(id);
    } catch (e) {
      console.error('Failed to escalate', e);
      fetchConsistency();
    }
  };

  const fetchPipeline = async () => {
    try {
      const data = await api.observability.pipeline();
      const stages = data.stages || [];
      setActiveStage(data.active_stage || 'Idle');
      setFunnelStages(stages.map((s: any) => ({
        name: s.name,
        throughput: s.processed_count.toString(),
        errorRate: s.error_rate || '0.0%',
        status: s.processed_count > 0 ? 'confirmed' : 'neutral'
      })));
    } catch (e) {
      console.error('Failed to fetch pipeline', e);
    }
  };

  const fetchConsistency = async () => {
    try {
      const data = await api.observability.consistency();
      setMismatches(data.mismatches || []);
    } catch (e) {
      console.error('Failed to fetch consistency', e);
    }
  };

  const fetchQuality = async () => {
    try {
      const data = await api.observability.qualityMetrics();
      setQualityMetrics(data);
    } catch (e) {
      console.error('Failed to fetch quality metrics', e);
    }
  };

  const refreshData = async () => {
    setIsRefreshing(true);
    if (activeTab === 'pipeline') await fetchPipeline();
    if (activeTab === 'consistency') await fetchConsistency();
    if (activeTab === 'quality') await fetchQuality();
    if (activeTab === 'export') await fetchExportMetrics();
    if (activeTab === 'llm') await fetchLlmTelemetry();
    setIsRefreshing(false);
  };

  React.useEffect(() => {
    refreshData();
    // Refresh every 1 second for live processing feel when on pipeline tab
    const interval = setInterval(refreshData, activeTab === 'pipeline' ? 1000 : 5000);
    return () => clearInterval(interval);
  }, [activeTab]);

  return (
    <div className="space-y-6 h-full flex flex-col">
      <style>{`
        @keyframes active-progress {
          0% { width: 0%; }
          100% { width: 92%; }
        }
        .progress-bar-active {
          animation: active-progress 180s cubic-bezier(0.05, 0.7, 0.1, 1) forwards;
        }
      `}</style>
      <div className="flex items-center justify-between shrink-0">
        <h1 className="text-display text-ink tracking-tight flex items-center">
          <Activity className="mr-4 h-8 w-8 text-ledger" />
          Observability
          {activeStage !== 'Idle' && (
            <span className="ml-4 inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-signal/10 text-signal text-caption font-medium border border-signal/20 animate-pulse">
              <span className="h-2 w-2 rounded-full bg-signal"></span>
              Live: Processing {activeStage}
            </span>
          )}
        </h1>
        <Button variant="secondary" size="sm" onClick={refreshData} disabled={isRefreshing}>
          <RefreshCw className={`mr-2 h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      <div className="flex border-b border-hairline shrink-0">
        {(['pipeline', 'consistency', 'quality', 'export', 'llm'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 font-medium text-body capitalize border-b-2 transition-colors ${
              activeTab === tab 
                ? 'border-ledger text-ledger' 
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab === 'quality' ? 'Data Quality' : tab === 'export' ? 'Export Metrics' : tab === 'llm' ? 'LLM Telemetry' : tab}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto pb-8">
        {activeTab === 'pipeline' && (
          <div className="space-y-8 max-w-5xl">
            <h2 className="text-h2 text-ink">Pipeline Stage Funnel</h2>
            <div className="flex flex-col space-y-2">
              {(() => {
                const STAGE_ORDER = ['Ingestion', 'Chunking', 'Extraction', 'Resolution', 'Consolidation', 'Community Detection', 'Export'];
                
                return funnelStages.map((stage) => {
                  const isCurrentlyActive = activeStage === stage.name;
                  
                  // Compute progress level for this stage
                  const getStageProgress = () => {
                    if (activeStage === 'Idle') {
                      // If the stage has run previously and has throughput, mark as completed
                      const hasRun = parseInt(stage.throughput) > 0;
                      return hasRun ? 100 : 0;
                    }
                    
                    const activeIndex = STAGE_ORDER.indexOf(activeStage);
                    const currentIndex = STAGE_ORDER.indexOf(stage.name);
                    
                    if (currentIndex < activeIndex) {
                      return 100;
                    }
                    
                    // Special case: Extraction, Resolution, and Consolidation are computed in parallel
                    // during the same processing loop, so they advance together.
                    if (activeStage === 'Extraction' && (stage.name === 'Resolution' || stage.name === 'Consolidation')) {
                      return 'active';
                    }
                    
                    if (currentIndex === activeIndex) {
                      return 'active';
                    }
                    
                    return 0;
                  };

                  const progress = getStageProgress();
                  let progressBarClass = "absolute left-0 top-0 bottom-0 transition-all duration-1000";
                  let progressBarStyle: React.CSSProperties = {};

                  if (progress === 100) {
                    progressBarClass += " bg-emerald-500/20 border-r-2 border-emerald-500/40 w-full";
                  } else if (progress === 'active') {
                    progressBarClass += " bg-emerald-500/15 border-r border-emerald-500/30 progress-bar-active";
                  } else {
                    progressBarStyle = { width: '0%' };
                  }

                  return (
                    <div key={stage.name} className="flex items-center">
                      <div className={`w-48 text-right pr-6 font-medium ${isCurrentlyActive ? 'text-signal' : 'text-ink'}`}>
                        {stage.name}
                      </div>
                      <div className={`flex-1 h-16 rounded-r-lg border-y border-r relative flex items-center px-6 overflow-hidden transition-all ${
                        isCurrentlyActive 
                          ? 'bg-signal/5 border-signal/30 ring-1 ring-inset ring-signal/20' 
                          : 'bg-paper border-hairline'
                      }`}>
                        {/* Smooth Green Progress Bar Overlay */}
                        <div className={progressBarClass} style={progressBarStyle} />

                        <div className="relative z-10 flex w-full justify-between items-center">
                          <div className="text-body text-ink font-mono">{stage.throughput} <span className="text-caption text-ink-muted ml-1">throughput</span></div>
                          <div className="flex items-center gap-4">
                            {isCurrentlyActive && (
                               <span className="text-caption font-bold text-signal animate-pulse flex items-center gap-2 uppercase tracking-widest">
                                 <RefreshCw className="h-3 w-3 animate-spin" /> Processing
                               </span>
                            )}
                            <span className="text-body text-ink font-mono">{stage.errorRate} <span className="text-caption text-ink-muted ml-1">error</span></span>
                            <Badge status={isCurrentlyActive ? 'signal' : stage.status}>{isCurrentlyActive ? 'Active' : (stage.status === 'confirmed' ? 'Healthy' : stage.status === 'signal' ? 'Warning' : 'Idle')}</Badge>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </div>
        )}

        {activeTab === 'consistency' && (
           <div className="space-y-6 max-w-4xl">
             <div className="flex justify-between items-end">
               <h2 className="text-h2 text-ink">Consistency Monitor</h2>
               <div className="text-caption text-ink-muted font-mono">Status: Active</div>
             </div>
             
             {mismatches.length > 0 ? (
               <div className="border border-alert rounded-lg bg-alert/5 p-6 space-y-4">
                 <div className="flex items-center gap-3">
                   <ServerCrash className="h-6 w-6 text-alert" />
                   <h3 className="text-h2 text-alert">{mismatches.length} Mismatches Detected</h3>
                 </div>
                 <p className="text-body text-ink">
                   Engineering attention is required. The dual-store commit log contains records that are stuck in a pending state across stores.
                 </p>
                 
                 <div className="bg-white border border-alert/30 rounded-md divide-y divide-alert/30">
                   {mismatches.map(m => (
                     <div key={m.id} className="p-4 flex justify-between items-center">
                       <div>
                         <div className="font-mono text-body text-ink mb-1">{m.txId}</div>
                         <div className="text-caption text-ink-muted">stuck_pending in {m.store} for {m.durationMinutes} minutes</div>
                       </div>
                       <Button variant="danger" size="sm" onClick={() => escalate(m.id)}>Escalate</Button>
                     </div>
                   ))}
                 </div>
               </div>
             ) : (
               <div className="p-8 text-center text-ink-muted border border-hairline bg-white rounded-lg italic">
                 No consistency mismatches or stuck transactions detected.
               </div>
             )}
           </div>
        )}

        {activeTab === 'quality' && (
          <div className="space-y-6">
            <h2 className="text-h2 text-ink">Data Quality Metrics</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              <TrendCard title="Source Freshness" value={qualityMetrics.source_freshness || '-'} colorClass="var(--color-confirmed)" />
              <TrendCard title="CDC Skip Rate" value={qualityMetrics.cdc_skip_rate ?? 'N/A'} colorClass="var(--color-confirmed)" />
              <TrendCard title="Parse Failure Rate" value={qualityMetrics.parse_failure_rate ?? 'N/A'} colorClass="var(--color-signal)" />
              <TrendCard title="Temp. Consistency" value={qualityMetrics.temporal_consistency ?? '-'} colorClass="var(--color-confirmed)" />
              
              <TrendCard title="Review Rate" value={`${((qualityMetrics.review_rate || 0) * 100).toFixed(1)}%`} colorClass="var(--color-ink-muted)" />
              <TrendCard title="Conflict Rate" value={qualityMetrics.active_edge_conflict_rate ?? 'N/A'} colorClass="var(--color-alert)" />

              <TrendCard
                title="Stub Fallback Rate"
                value={qualityMetrics.stub_fallback_rate ?? 'N/A'}
                colorClass="var(--color-alert)"
                label="prompts answered generically"
              />
              <TrendCard
                title="Pre-ingest Drop Rate"
                value={qualityMetrics.prefilter_drop_rate ?? 'N/A'}
                colorClass="var(--color-signal)"
                label={`${qualityMetrics.episodes_avoided ?? 0} LLM calls avoided`}
              />
            </div>

            <p className="text-caption text-ink-muted max-w-3xl">
              Stub fallback rate is the share of Graphiti prompts the offline client could not
              answer specifically. Anything above zero means part of the graph was built from a
              generic response rather than the document.
            </p>
          </div>
        )}

        {activeTab === 'export' && (
          <div className="space-y-6 max-w-4xl">
            <h2 className="text-h2 text-ink">Export Observability</h2>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
              <div className="border border-hairline rounded-lg bg-white p-6">
                <div className="text-caption text-ink-muted uppercase font-medium tracking-wider mb-2">Last Generation Outcome</div>
                {exportMetrics?.last_generation ? (
                  <div className="mt-4 space-y-2">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-body text-ink">{exportMetrics.last_generation.version}</span>
                      <Badge status={exportMetrics.last_generation.outcome === 'passed' ? 'confirmed' : 'alert'}>
                        {exportMetrics.last_generation.outcome}
                      </Badge>
                    </div>
                    <div className="text-caption text-ink-muted">
                      {exportMetrics.last_generation.entity_count} entities, {exportMetrics.last_generation.edge_count} facts
                      {exportMetrics.last_generation.removed_edges > 0 &&
                        `, ${exportMetrics.last_generation.removed_edges} withheld by policy`}
                    </div>
                    <div className="text-caption text-ink-muted">{exportMetrics.last_generation.created_at}</div>
                  </div>
                ) : (
                  <div className="text-caption text-ink-muted italic mt-4">No snapshot generations recorded yet.</div>
                )}
              </div>

              <div className="border border-hairline rounded-lg bg-white p-6">
                <div className="flex justify-between items-start">
                  <div className="text-caption text-ink-muted uppercase font-medium tracking-wider mb-2">Last Downstream Fetch</div>
                  <AlertTriangle className="h-5 w-5 text-ink-muted" />
                </div>
                {exportMetrics?.last_downstream_fetch ? (
                  <div className="mt-4 space-y-2">
                    <div className="font-mono text-body text-ink">{exportMetrics.last_downstream_fetch.version}</div>
                    <div className="text-caption text-ink-muted">{exportMetrics.last_downstream_fetch.fetched_at}</div>
                  </div>
                ) : (
                  <div className="text-caption text-ink-muted italic mt-4">No downstream fetch recorded.</div>
                )}
              </div>
            </div>

            <div className="border border-hairline rounded-lg bg-white p-6">
               <div className="text-caption text-ink-muted uppercase font-medium tracking-wider mb-6">
                 Snapshot Size Trend
                 {exportMetrics ? ` (${exportMetrics.total_snapshots} snapshot${exportMetrics.total_snapshots === 1 ? '' : 's'})` : ''}
               </div>
               {exportMetrics && exportMetrics.size_trend.length > 1 ? (
                 <div className="h-48 w-full">
                   <Sparkline
                     data={exportMetrics.size_trend.map(s => s.byte_size)}
                     color="var(--color-ledger)"
                     height={192}
                   />
                 </div>
               ) : (
                 <div className="h-48 w-full flex items-center justify-center text-caption text-ink-muted italic border border-dashed border-hairline rounded">
                   {exportMetrics && exportMetrics.size_trend.length === 1
                     ? 'A trend needs at least two snapshots.'
                     : 'No historical size trend data available.'}
                 </div>
               )}
            </div>
          </div>
        )}

        {activeTab === 'llm' && (
          <div className="space-y-6 max-w-6xl">
            <h2 className="text-h2 text-ink">LLM Telemetry & Costs</h2>
            
            {llmTelemetry?.aggregate ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
                <TrendCard title="Total LLM Calls" value={llmTelemetry.aggregate.total_calls} colorClass="var(--color-ledger)" />
                <TrendCard title="Total Cost" value={`$${llmTelemetry.aggregate.total_cost_usd.toFixed(6)}`} colorClass="var(--color-alert)" />
                <TrendCard title="Avg Latency" value={llmTelemetry.aggregate.avg_latency_ms.toFixed(0)} unit="ms" colorClass="var(--color-signal)" />
                <TrendCard title="Total Tokens" value={(llmTelemetry.aggregate.total_tokens / 1000).toFixed(1)} unit="k" colorClass="var(--color-ledger)" />
                <TrendCard title="Nodes Extracted" value={llmTelemetry.aggregate.total_nodes_yield} colorClass="var(--color-confirmed)" />
                <TrendCard title="Edges Extracted" value={llmTelemetry.aggregate.total_edges_yield} colorClass="var(--color-confirmed)" />
              </div>
            ) : (
              <div className="p-4 text-center text-ink-muted border border-hairline bg-white rounded-lg italic">
                Loading aggregate stats...
              </div>
            )}

            <div className="mt-8 border border-hairline rounded-lg bg-white overflow-hidden">
              <div className="px-6 py-4 border-b border-hairline flex justify-between items-center bg-paper/50">
                <h3 className="text-h3 text-ink">Recent LLM Calls</h3>
                <div className="text-caption text-ink-muted font-mono">{llmTelemetry?.recent_calls?.length || 0} recent traces</div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-body">
                  <thead>
                    <tr className="border-b border-hairline text-ink-muted text-caption bg-paper/20">
                      <th className="px-4 py-3 font-medium">Prompt</th>
                      <th className="px-4 py-3 font-medium">Model</th>
                      <th className="px-4 py-3 font-medium">Latency</th>
                      <th className="px-4 py-3 font-medium">Data/Token Ratio</th>
                      <th className="px-4 py-3 font-medium">Yield</th>
                      <th className="px-4 py-3 font-medium text-right">Cost</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-hairline">
                    {llmTelemetry?.recent_calls?.map((call, i) => {
                      const dataToTokenRatio = call.input_tokens > 0 
                        ? (call.input_chars / call.input_tokens).toFixed(1) 
                        : '0';
                      const hasError = !!call.error;
                      
                      return (
                        <tr key={call.id || i} className={`hover:bg-paper/30 transition-colors ${hasError ? 'bg-alert/5' : ''}`}>
                          <td className="px-4 py-3">
                            <div className="font-mono text-ink text-sm truncate max-w-xs" title={call.prompt_name}>
                              {call.prompt_name}
                            </div>
                            {hasError && <div className="text-caption text-alert truncate max-w-xs mt-1" title={call.error || undefined}>{call.error}</div>}
                          </td>
                          <td className="px-4 py-3 text-sm text-ink-muted">{call.model_name}</td>
                          <td className="px-4 py-3 font-mono">{call.latency_ms}ms</td>
                          <td className="px-4 py-3">
                            <div className="font-mono text-ink-muted">{dataToTokenRatio} ch/tok</div>
                            <div className="text-caption text-ink-muted mt-0.5">{call.total_tokens} total tok</div>
                          </td>
                          <td className="px-4 py-3">
                            {(call.nodes_yield > 0 || call.edges_yield > 0) ? (
                              <div className="flex gap-2">
                                {call.nodes_yield > 0 && <Badge status="confirmed">{call.nodes_yield} N</Badge>}
                                {call.edges_yield > 0 && <Badge status="signal">{call.edges_yield} E</Badge>}
                              </div>
                            ) : (
                              <span className="text-caption text-ink-muted">-</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-right font-mono text-alert">
                            ${call.cost_usd.toFixed(6)}
                          </td>
                        </tr>
                      );
                    })}
                    {!llmTelemetry?.recent_calls?.length && (
                      <tr>
                        <td colSpan={6} className="px-4 py-8 text-center text-ink-muted italic">
                          No LLM calls recorded yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
