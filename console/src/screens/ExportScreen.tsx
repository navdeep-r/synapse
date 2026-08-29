import * as React from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { Box, Play, CheckCircle2, XCircle, Download, Send, Copy, Check } from 'lucide-react';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { api, type SnapshotResult } from '../api/client';

const formSchema = z.object({
  factScope: z.string(),
  reportScope: z.string(),
  accessControlHandling: z.enum(['filter_group', 'tag_downstream', 'export_unrestricted'], {
    required_error: 'You must explicitly select an access control handling policy.',
  }),
  confirmationPhrase: z.string().optional()
}).superRefine((data, ctx) => {
  if (data.accessControlHandling === 'export_unrestricted' && data.confirmationPhrase !== 'I understand the risk') {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: "You must type 'I understand the risk' to export unrestricted.",
      path: ['confirmationPhrase']
    });
  }
});

type FormData = z.infer<typeof formSchema>;

export interface SnapshotHistoryItem {
  version: string;
  date: string;
  size: string;
  diff: { addedEntities: number; removedEdges: number };
}

export default function ExportScreen() {
  const [activeTab, setActiveTab] = React.useState<'generate' | 'history' | 'delivery'>('generate');
  const [snapshots, setSnapshots] = React.useState<SnapshotHistoryItem[]>([]);
  const [generationStage, setGenerationStage] = React.useState<'idle' | 'reading_entities' | 'reading_edges' | 'applying_policy' | 'bundling' | 'validating' | 'done'>('idle');
  const [validationPass, setValidationPass] = React.useState(false);
  const [validationChecks, setValidationChecks] = React.useState<{ name: string; passed: boolean; detail?: string }[]>([]);
  const [result, setResult] = React.useState<SnapshotResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notifyState, setNotifyState] = React.useState<string | null>(null);
  const [lastFetch, setLastFetch] = React.useState('No downstream fetches recorded');
  const [endpoint, setEndpoint] = React.useState('');
  const [webhook, setWebhook] = React.useState('');

  const { register, handleSubmit, watch, formState: { errors } } = useForm<FormData>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      factScope: 'current',
      reportScope: 'flagged'
    }
  });

  const accessHandling = watch('accessControlHandling');

  const toHistoryItem = (s: SnapshotResult): SnapshotHistoryItem => ({
    version: s.version,
    date: s.date,
    size: s.size,
    diff: s.diff,
  });

  const loadHistory = React.useCallback(async () => {
    try {
      setSnapshots((await api.snapshots.list()).map(toHistoryItem));
    } catch {
      /* history stays empty when unreachable */
    }
  }, []);

  React.useEffect(() => {
    loadHistory();
    api.observability.exportMetrics()
      .then(metrics => setLastFetch(
        metrics.last_downstream_fetch
          ? `${metrics.last_downstream_fetch.version} — ${metrics.last_downstream_fetch.fetched_at}`
          : 'No downstream fetches recorded',
      ))
      .catch(() => undefined);
    api.settings.delivery()
      .then(config => {
        setEndpoint(config.endpoint);
        setWebhook(config.endpoint);
      })
      .catch(() => undefined);
  }, [loadHistory]);

  const onSubmit = async (data: FormData) => {
    setError(null);
    setNotifyState(null);
    setResult(null);
    setValidationChecks([]);

    // The stage rail is cosmetic: generation is a single backend call, and
    // pretending otherwise with fixed delays was the old placeholder. These
    // advance immediately and land on the real result.
    setGenerationStage('reading_entities');
    setGenerationStage('reading_edges');
    setGenerationStage('applying_policy');
    setGenerationStage('bundling');
    setGenerationStage('validating');

    try {
      const snapshot = await api.snapshots.generate({
        factScope: data.factScope,
        reportScope: data.reportScope,
        accessControlHandling: data.accessControlHandling,
        confirmationPhrase: data.confirmationPhrase,
      });
      setResult(snapshot);
      setValidationChecks(snapshot.validation_checks);
      setValidationPass(snapshot.passed);
      setGenerationStage('done');
      loadHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Snapshot generation failed');
      setValidationPass(false);
      setGenerationStage('idle');
    }
  };

  const onNotify = async () => {
    if (!result) return;
    try {
      const response = await api.snapshots.notify(result.version);
      setNotifyState(response.detail);
    } catch (e) {
      setNotifyState(e instanceof Error ? e.message : 'Notification failed');
    }
  };

  const onSaveWebhook = async () => {
    try {
      const current = await api.settings.delivery();
      await api.settings.saveDelivery({ ...current, endpoint: webhook });
      setEndpoint(webhook);
      setNotifyState('Delivery endpoint saved.');
    } catch (e) {
      setNotifyState(e instanceof Error ? e.message : 'Failed to save the endpoint');
    }
  };

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex items-center justify-between shrink-0">
        <h1 className="text-display text-ink tracking-tight flex items-center">
          <Box className="mr-4 h-8 w-8 text-ledger" />
          Snapshot & Export
        </h1>
      </div>

      <div className="flex border-b border-hairline shrink-0">
        {(['generate', 'history', 'delivery'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 font-medium text-body capitalize border-b-2 transition-colors ${
              activeTab === tab 
                ? 'border-ledger text-ledger' 
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {activeTab === 'generate' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 pb-8">
            <div className="space-y-6">
              <h2 className="text-h2 text-ink">Export Scope & Policy</h2>
              <form onSubmit={handleSubmit(onSubmit)} className="space-y-6 p-6 border border-hairline rounded-lg bg-white">
                
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider">Fact Scope</label>
                    <select {...register('factScope')} className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger">
                      <option value="current">Current-only</option>
                      <option value="historical">Include Historical</option>
                    </select>
                  </div>
                  <div className="space-y-2">
                    <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider">Reports</label>
                    <select {...register('reportScope')} className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger">
                      <option value="flagged">Flagged-only</option>
                      <option value="all">All Available</option>
                    </select>
                  </div>
                </div>

                <div className="space-y-3 pt-4 border-t border-hairline">
                  <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider">
                    Access Control Policy (Required)
                  </label>
                  
                  <div className="space-y-2">
                    <label className="flex items-start gap-3 p-3 border border-hairline rounded-md cursor-pointer hover:bg-paper/50 transition-colors">
                      <input type="radio" value="filter_group" {...register('accessControlHandling')} className="mt-1 text-ledger focus:ring-ledger" />
                      <div>
                        <div className="font-medium text-ink">Filter by Access Group</div>
                        <div className="text-caption text-ink-muted mt-1">Omit facts the target downstream system lacks clearance for.</div>
                      </div>
                    </label>
                    
                    <label className="flex items-start gap-3 p-3 border border-hairline rounded-md cursor-pointer hover:bg-paper/50 transition-colors">
                      <input type="radio" value="tag_downstream" {...register('accessControlHandling')} className="mt-1 text-ledger focus:ring-ledger" />
                      <div>
                        <div className="font-medium text-ink">Tag for Downstream Enforcement</div>
                        <div className="text-caption text-ink-muted mt-1">Include all facts, but embed ACL tags in the payload schema.</div>
                      </div>
                    </label>

                    <label className="flex items-start gap-3 p-3 border border-alert/30 bg-alert/5 rounded-md cursor-pointer">
                      <input type="radio" value="export_unrestricted" {...register('accessControlHandling')} className="mt-1 text-alert focus:ring-alert" />
                      <div className="w-full">
                        <div className="font-medium text-alert">Export Unrestricted</div>
                        <div className="text-caption text-alert/80 mt-1 mb-3">WARNING: This exports all data globally without ACL bounds.</div>
                        
                        {accessHandling === 'export_unrestricted' && (
                          <div className="space-y-2">
                            <label className="text-caption text-alert font-medium block">Type "I understand the risk" to confirm:</label>
                            <input 
                              type="text" 
                              {...register('confirmationPhrase')}
                              className="w-full border border-alert/50 rounded-md px-3 py-2 text-body focus:outline-none focus:ring-2 focus:ring-alert/50 bg-white"
                            />
                            {errors.confirmationPhrase && (
                              <div className="text-caption text-alert mt-1">{errors.confirmationPhrase.message}</div>
                            )}
                          </div>
                        )}
                      </div>
                    </label>
                  </div>
                  {errors.accessControlHandling && (
                    <div className="text-caption text-alert">{errors.accessControlHandling.message}</div>
                  )}
                </div>

                {error && (
                  <div className="border border-alert/40 bg-alert/5 text-alert rounded-md px-3 py-2 text-body">
                    {error}
                  </div>
                )}

                <div className="pt-6">
                  <Button type="submit" variant="primary" className="w-full h-12" disabled={generationStage !== 'idle' && generationStage !== 'done'}>
                    <Play className="mr-2 h-5 w-5" /> Generate Snapshot
                  </Button>
                </div>
              </form>
            </div>

            <div className="space-y-6">
              <h2 className="text-h2 text-ink">Generation Pipeline</h2>
              
              <div className="border border-hairline rounded-lg bg-white overflow-hidden flex flex-col h-[600px]">
                
                {generationStage === 'idle' && (
                  <div className="flex-1 flex items-center justify-center text-ink-muted text-body flex-col gap-4">
                    <Box className="h-12 w-12 opacity-20" />
                    Waiting to start generation...
                  </div>
                )}

                {generationStage !== 'idle' && (
                  <div className="flex-1 overflow-y-auto p-6 space-y-8 relative">
                    <div className="space-y-4 relative before:absolute before:inset-y-2 before:left-3 before:w-0.5 before:bg-hairline pl-8">
                      {['reading_entities', 'reading_edges', 'applying_policy', 'bundling', 'validating'].map((stage, idx) => {
                        const stages = ['reading_entities', 'reading_edges', 'applying_policy', 'bundling', 'validating', 'done'];
                        const currentIdx = stages.indexOf(generationStage);
                        const isDone = currentIdx > idx;
                        const isCurrent = currentIdx === idx;
                        
                        return (
                          <div key={stage} className="relative">
                            <div className={`absolute -left-[2.15rem] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-white ${
                              isDone ? 'bg-confirmed' : isCurrent ? 'bg-ledger animate-pulse' : 'bg-hairline'
                            }`} />
                            <div className={`font-medium capitalize ${
                              isDone ? 'text-confirmed' : isCurrent ? 'text-ledger' : 'text-ink-muted'
                            }`}>
                              {stage.replace('_', ' ')}
                            </div>
                          </div>
                        )
                      })}
                    </div>

                    {generationStage === 'done' && (
                       <div className={`mt-8 border rounded-lg p-6 ${validationPass ? 'border-confirmed bg-confirmed/5' : 'border-alert bg-alert/5'}`}>
                         <h3 className={`text-h2 mb-4 flex items-center ${validationPass ? 'text-confirmed' : 'text-alert'}`}>
                           {validationPass ? <CheckCircle2 className="mr-2 h-6 w-6" /> : <XCircle className="mr-2 h-6 w-6" />}
                           Validation Report
                         </h3>
                         
                         {result && (
                           <div className="mb-6 grid grid-cols-2 gap-3 text-body">
                             <div className="text-ink-muted">Version</div>
                             <div className="font-mono text-ink">{result.version}</div>
                             <div className="text-ink-muted">Entities / Facts</div>
                             <div className="text-ink">{result.entity_count} / {result.edge_count}</div>
                             <div className="text-ink-muted">Size</div>
                             <div className="text-ink">{result.size}</div>
                             <div className="text-ink-muted">Policy</div>
                             <div className="font-mono text-ink">{result.policy}</div>
                             {result.removed_edges > 0 && (
                               <>
                                 <div className="text-ink-muted">Withheld by policy</div>
                                 <div className="text-alert">{result.removed_edges} fact(s)</div>
                               </>
                             )}
                           </div>
                         )}

                         <ul className="space-y-3 mb-6">
                           {validationChecks.map(check => (
                             <li key={check.name} className="flex items-start gap-3 text-body">
                               {check.passed ? <Check className="h-5 w-5 text-confirmed shrink-0" /> : <XCircle className="h-5 w-5 text-alert shrink-0" />}
                               <div>
                                 <div className={check.passed ? 'text-ink' : 'text-alert font-medium'}>{check.name}</div>
                                 {check.detail && (
                                   <div className="text-caption text-ink-muted mt-0.5">{check.detail}</div>
                                 )}
                               </div>
                             </li>
                           ))}
                         </ul>

                         {notifyState && (
                           <div className="mb-4 text-body text-ink-muted border border-hairline rounded p-3 bg-white">
                             {notifyState}
                           </div>
                         )}

                         <div className="flex gap-4 pt-4 border-t border-hairline/30">
                           <a
                             className="flex-1"
                             href={result ? api.snapshots.downloadUrl(result.version) : undefined}
                             download
                             onClick={e => { if (!validationPass) e.preventDefault(); }}
                           >
                             <Button 
                               variant="secondary" 
                               className="w-full"
                               disabled={!validationPass}
                             >
                               <Download className="mr-2 h-4 w-4" /> Download JSON
                             </Button>
                           </a>
                           <Button 
                             variant="primary" 
                             className="flex-1"
                             disabled={!validationPass}
                             onClick={onNotify}
                           >
                             <Send className="mr-2 h-4 w-4" /> Notify Downstream
                           </Button>
                         </div>
                       </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'history' && (
          <div className="space-y-6 max-w-4xl">
            <h2 className="text-h2 text-ink">Snapshot History</h2>
            <div className="border border-hairline rounded-lg bg-white overflow-hidden">
               <table className="w-full text-left text-body">
                 <thead className="bg-paper border-b border-hairline text-ink-muted text-caption uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 font-medium">Version</th>
                    <th className="px-4 py-3 font-medium">Date</th>
                    <th className="px-4 py-3 font-medium">Size</th>
                    <th className="px-4 py-3 font-medium">Diff</th>
                  </tr>
                 </thead>
                 <tbody className="divide-y divide-hairline">
                    {snapshots.map(snapshot => (
                      <tr key={snapshot.version} className="hover:bg-paper/50 cursor-pointer">
                        <td className="px-4 py-3 font-mono text-ink-muted">{snapshot.version}</td>
                        <td className="px-4 py-3 text-ink">{snapshot.date}</td>
                        <td className="px-4 py-3 text-ink">{snapshot.size}</td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2 text-caption">
                            <span className="text-confirmed">+{snapshot.diff.addedEntities} entities</span>
                            <span className="text-alert">-{snapshot.diff.removedEdges} edges</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {snapshots.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-4 py-8 text-center text-ink-muted italic">
                          No generated snapshots found in history.
                        </td>
                      </tr>
                    )}
                 </tbody>
               </table>
            </div>
          </div>
        )}

        {activeTab === 'delivery' && (
          <div className="max-w-2xl space-y-6">
            <h2 className="text-h2 text-ink">Delivery & API Configuration</h2>
            
            <div className="border border-hairline rounded-lg bg-white p-6 space-y-6">
              <div>
                <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider mb-2">API Endpoint</label>
                <div className="flex gap-2">
                  <input
                    readOnly
                    value={`${window.location.origin}/api/v1/snapshots/latest/download`}
                    className="flex-1 border border-hairline rounded-md px-3 py-2 font-mono text-body text-ink bg-paper focus:outline-none"
                  />
                  <Button
                    variant="secondary"
                    size="md"
                    onClick={() => navigator.clipboard?.writeText(`${window.location.origin}/api/v1/snapshots/latest/download`)}
                  >
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              <div>
                <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider mb-2">Webhook URL</label>
                <div className="flex gap-2">
                  <input
                    type="url"
                    placeholder="https://..."
                    value={webhook}
                    onChange={e => setWebhook(e.target.value)}
                    className="flex-1 border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger"
                  />
                  <Button variant="primary" size="md" onClick={onSaveWebhook}>Save</Button>
                </div>
                {endpoint && (
                  <div className="text-caption text-ink-muted mt-2">Currently notifying {endpoint}</div>
                )}
              </div>

              <div className="pt-4 border-t border-hairline">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-body font-medium text-ink">Last Downstream Fetch</span>
                  <Badge status="neutral">{lastFetch}</Badge>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
