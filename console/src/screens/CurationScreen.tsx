import * as React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Filter, Check, ShieldAlert, GitMerge, Settings2, RefreshCw } from 'lucide-react';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import {
  api,
  type AclIssue,
  type CalibrationItem as CalibrationRecord,
  type CardinalityConflict,
  type Contradiction,
  type MergeCandidate,
  type QuarantineRecord,
} from '../api/client';

export interface QueueCandidate {
  id: string;
  type: string;
  primary: string;
  secondary: string;
  confidence: number;
  reason: string;
}

export interface CalibrationItem {
  id: string;
  title: string;
  currentVal: number;
  recommendedVal: number;
  recallImpact: string;
}

const toQueueCandidate = (c: MergeCandidate): QueueCandidate => ({
  id: c.id,
  type: c.entity_type,
  primary: c.primary_name,
  secondary: c.secondary_name,
  confidence: c.confidence,
  reason: c.reason,
});

const toCalibrationItem = (c: CalibrationRecord): CalibrationItem => ({
  id: c.id,
  title: c.title,
  currentVal: c.current_val,
  recommendedVal: c.recommended_val,
  recallImpact: c.recall_impact,
});

export default function CurationScreen() {
  const [activeTab, setActiveTab] = React.useState<'review' | 'quarantine' | 'calibration'>('review');
  const [quarantineTab, setQuarantineTab] = React.useState<'records' | 'contradictions' | 'conflicts' | 'acl'>('contradictions');
  const [queue, setQueue] = React.useState<QueueCandidate[]>([]);
  const [selectedQueueItem, setSelectedQueueItem] = React.useState<string | null>(null);
  const [calibrationItems, setCalibrationItems] = React.useState<CalibrationItem[]>([]);
  const [records, setRecords] = React.useState<QuarantineRecord[]>([]);
  const [contradictions, setContradictions] = React.useState<Contradiction[]>([]);
  const [conflicts, setConflicts] = React.useState<CardinalityConflict[]>([]);
  const [aclIssues, setAclIssues] = React.useState<AclIssue[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const loadQueue = React.useCallback(async () => {
    try {
      const candidates = await api.curation.queue();
      setQueue(candidates.map(toQueueCandidate));
      setSelectedQueueItem(current =>
        current && candidates.some(c => c.id === current) ? current : candidates[0]?.id ?? null,
      );
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load the review queue');
    }
  }, []);

  const loadQuarantine = React.useCallback(async () => {
    try {
      const [recordsData, contradictionsData, conflictsData, aclData] = await Promise.all([
        api.curation.quarantine(),
        api.curation.contradictions(),
        api.curation.conflicts(),
        api.curation.aclIssues(),
      ]);
      setRecords(recordsData);
      setContradictions(contradictionsData);
      setConflicts(conflictsData);
      setAclIssues(aclData);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load quarantine');
    }
  }, []);

  const loadCalibration = React.useCallback(async () => {
    try {
      setCalibrationItems((await api.curation.calibration()).map(toCalibrationItem));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load calibration');
    }
  }, []);

  const refresh = React.useCallback(async () => {
    setIsLoading(true);
    await Promise.all([loadQueue(), loadQuarantine(), loadCalibration()]);
    setIsLoading(false);
  }, [loadQueue, loadQuarantine, loadCalibration]);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  const handleQueueAction = React.useCallback(async (id: string, action: 'merge' | 'distinct') => {
    // Remove optimistically: the decision is durable server-side, and waiting
    // for the round trip would break the keyboard-driven review rhythm.
    const previous = queue;
    const remaining = queue.filter(item => item.id !== id);
    setQueue(remaining);
    if (selectedQueueItem === id) setSelectedQueueItem(remaining[0]?.id ?? null);

    try {
      if (action === 'merge') await api.curation.merge(id);
      else await api.curation.distinct(id);
      setError(null);
    } catch (e) {
      setQueue(previous);
      setError(e instanceof Error ? e.message : 'Failed to record the decision');
    }
  }, [queue, selectedQueueItem]);

  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (activeTab !== 'review' || !selectedQueueItem) return;
      if (e.key === 'm') handleQueueAction(selectedQueueItem, 'merge');
      if (e.key === 'd') handleQueueAction(selectedQueueItem, 'distinct');
      if (e.key === 'j') {
        const idx = queue.findIndex(i => i.id === selectedQueueItem);
        if (idx < queue.length - 1) setSelectedQueueItem(queue[idx+1].id);
      }
      if (e.key === 'k') {
        const idx = queue.findIndex(i => i.id === selectedQueueItem);
        if (idx > 0) setSelectedQueueItem(queue[idx-1].id);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeTab, selectedQueueItem, queue, handleQueueAction]);

  const resolveCalibration = async (id: string, decision: 'approve' | 'dismiss') => {
    setCalibrationItems(items => items.filter(item => item.id !== id));
    try {
      if (decision === 'approve') await api.curation.approveCalibration(id);
      else await api.curation.dismissCalibration(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to record the calibration decision');
      loadCalibration();
    }
  };

  const quarantineCounts = {
    records: records.length,
    contradictions: contradictions.length,
    conflicts: conflicts.length,
    acl: aclIssues.length,
  };

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex items-center justify-between shrink-0">
        <h1 className="text-display text-ink tracking-tight">Curation</h1>
        <Button variant="ghost" size="sm" onClick={refresh} disabled={isLoading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {error && (
        <div className="shrink-0 border border-alert/40 bg-alert/5 text-alert rounded-md px-4 py-3 text-body">
          {error}
        </div>
      )}

      <div className="flex border-b border-hairline shrink-0">
        {(['review', 'quarantine', 'calibration'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 font-medium text-body capitalize border-b-2 transition-colors ${
              activeTab === tab 
                ? 'border-ledger text-ledger' 
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab === 'review' ? 'Review Queue' : tab === 'quarantine' ? 'Quarantine & Conflicts' : 'Threshold Calibration'}
          </button>
        ))}
      </div>

      {activeTab === 'review' && (
        <div className="flex-1 flex gap-6 overflow-hidden pb-8 h-[600px]">
          <div className="w-1/3 flex flex-col border border-hairline rounded-lg bg-white overflow-hidden h-[600px]">
            <div className="p-3 border-b border-hairline bg-paper/50 flex items-center justify-between">
              <span className="text-caption font-medium text-ink-muted uppercase">Pending: {queue.length}</span>
              <Filter className="h-4 w-4 text-ink-muted" />
            </div>
            <div className="flex-1 overflow-y-auto">
              <AnimatePresence>
                {queue.map(item => (
                  <motion.div
                    key={item.id}
                    layout
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: 100, transition: { duration: 0.2 } }}
                    onClick={() => setSelectedQueueItem(item.id)}
                    className={`p-4 border-b border-hairline cursor-pointer transition-colors ${
                      selectedQueueItem === item.id ? 'bg-ledger/5 border-l-2 border-l-ledger' : 'hover:bg-paper/50 border-l-2 border-l-transparent'
                    }`}
                  >
                    <div className="flex justify-between items-start mb-1">
                      <span className="text-caption text-ink-muted">Merge Candidate</span>
                      <span className="font-mono text-caption text-ink-muted">{(item.confidence * 100).toFixed(0)}%</span>
                    </div>
                    <div className="font-medium text-ink text-body truncate">{item.primary}</div>
                    <div className="text-ink-muted text-body truncate mt-1">vs {item.secondary}</div>
                  </motion.div>
                ))}
              </AnimatePresence>
              {queue.length === 0 && (
                <div className="p-8 text-center text-ink-muted text-body">Review queue empty</div>
              )}
            </div>
          </div>
          
          <div className="w-2/3 border border-hairline rounded-lg bg-white flex flex-col overflow-hidden h-[600px]">
            {selectedQueueItem && queue.find(i => i.id === selectedQueueItem) ? (() => {
              const item = queue.find(i => i.id === selectedQueueItem)!;
              return (
                <>
                  <div className="flex-1 overflow-y-auto p-8 flex flex-col">
                    <div className="flex justify-between items-start mb-8 shrink-0">
                      <h2 className="text-display text-ink">Merge Candidate Detail</h2>
                      <div className="flex gap-2 text-caption text-ink-muted font-mono">
                        <span>Keyboard: [M] Merge</span>
                        <span>[D] Distinct</span>
                      </div>
                    </div>
                    
                    <div className="flex gap-8 mb-8 shrink-0">
                      <div className="flex-1 border border-hairline rounded-lg p-4 bg-paper/30">
                        <div className="text-caption text-ink-muted uppercase mb-2 tracking-wider">Primary Entity</div>
                        <div className="text-h2 text-ink mb-4">{item.primary}</div>
                        <Badge status="neutral">{item.type}</Badge>
                      </div>
                      
                      <div className="flex items-center justify-center shrink-0">
                        <GitMerge className="h-8 w-8 text-ledger opacity-50" />
                      </div>

                      <div className="flex-1 border border-hairline rounded-lg p-4 bg-paper/30">
                        <div className="text-caption text-ink-muted uppercase mb-2 tracking-wider">Candidate</div>
                        <div className="text-h2 text-ink mb-4">{item.secondary}</div>
                        <Badge status="neutral">{(item.confidence * 100).toFixed(0)}% confidence</Badge>
                      </div>
                    </div>

                    <div className="border border-hairline rounded-lg p-4 bg-paper/20 shrink-0">
                      <div className="text-caption text-ink-muted uppercase mb-2 tracking-wider">
                        Why this was proposed
                      </div>
                      <p className="text-body text-ink whitespace-pre-wrap">{item.reason}</p>
                    </div>
                  </div>

                  <div className="p-8 pt-6 border-t border-hairline shrink-0 flex gap-4 bg-white/50 backdrop-blur-sm">
                    <Button 
                      variant="primary" 
                      className="flex-1 !bg-confirmed hover:!bg-confirmed/90 border border-confirmed"
                      onClick={() => handleQueueAction(item.id, 'merge')}
                    >
                      <Check className="mr-2 h-5 w-5" /> Confirm Merge
                    </Button>
                    <Button 
                      variant="secondary" 
                      className="flex-1 border border-alert text-alert hover:bg-alert/5 bg-white"
                      onClick={() => handleQueueAction(item.id, 'distinct')}
                    >
                      <ShieldAlert className="mr-2 h-5 w-5" /> Mark as Distinct
                    </Button>
                  </div>
                </>
              );
            })() : (
              <div className="h-full flex items-center justify-center text-ink-muted text-body p-8">
                Select an item to review
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'quarantine' && (
        <div className="space-y-6">
          <div className="flex gap-2">
            {(['records', 'contradictions', 'conflicts', 'acl'] as const).map(tab => (
              <Button
                key={tab}
                variant={quarantineTab === tab ? 'secondary' : 'ghost'}
                size="sm"
                onClick={() => setQuarantineTab(tab)}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1).replace('-', ' ')} ({quarantineCounts[tab]})
              </Button>
            ))}
          </div>

          {quarantineTab === 'records' && (
            <QuarantineList
              items={records}
              empty="No quarantined records. Documents that fail to parse and escalated mismatches appear here."
              render={record => (
                <>
                  <div className="flex justify-between items-start">
                    <div className="font-medium text-ink">{record.subject}</div>
                    <Badge status="signal">{record.kind.replace(/_/g, ' ')}</Badge>
                  </div>
                  <div className="text-body text-ink-muted mt-1">{record.detail}</div>
                  <div className="text-caption text-ink-muted font-mono mt-2">{record.created_at}</div>
                </>
              )}
            />
          )}

          {quarantineTab === 'contradictions' && (
            <QuarantineList
              items={contradictions}
              empty="No superseded facts. When newer information invalidates an existing fact, both versions appear here."
              render={item => (
                <>
                  <div className="flex justify-between items-start">
                    <div className="font-medium text-ink">
                      {item.subject} <span className="font-mono text-ink-muted">{item.relation_type}</span> {item.object}
                    </div>
                    <Badge status="signal">{item.status}</Badge>
                  </div>
                  <div className="text-body text-ink-muted mt-1">{item.fact}</div>
                  <div className="text-caption text-ink-muted font-mono mt-2">
                    valid {item.valid_at ?? 'unknown'} → invalid {item.invalid_at ?? 'unknown'}
                  </div>
                </>
              )}
            />
          )}

          {quarantineTab === 'conflicts' && (
            <QuarantineList
              items={conflicts}
              empty="No cardinality conflicts. The ontology's max_active_outgoing limits are all satisfied."
              render={item => (
                <>
                  <div className="flex justify-between items-start">
                    <div className="font-medium text-ink">
                      {item.entity_name} <span className="font-mono text-ink-muted">{item.relation_type}</span>
                    </div>
                    <Badge status="alert">
                      {item.targets.length} of max {item.max_active_outgoing}
                    </Badge>
                  </div>
                  <div className="text-body text-ink-muted mt-1">{item.detail}</div>
                  <div className="text-caption text-ink-muted mt-2">→ {item.targets.join(', ')}</div>
                </>
              )}
            />
          )}

          {quarantineTab === 'acl' && (
            <QuarantineList
              items={aclIssues}
              empty="No access control issues. No fact mixes source documents with different access tags."
              render={item => (
                <>
                  <div className="flex justify-between items-start">
                    <div className="font-medium text-ink">{item.fact}</div>
                    <Badge status="alert">{item.access_tags.join(' + ')}</Badge>
                  </div>
                  <div className="text-body text-ink-muted mt-1">{item.detail}</div>
                </>
              )}
            />
          )}
        </div>
      )}

      {activeTab === 'calibration' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {calibrationItems.map(item => (
            <div key={item.id} className="border border-hairline rounded-lg bg-white p-6 space-y-6">
               <div className="flex justify-between items-start">
                 <h3 className="text-h2 text-ink">{item.title}</h3>
                 <Settings2 className="h-5 w-5 text-ink-muted" />
               </div>
               
               <div className="space-y-2">
                 <div className="flex justify-between text-caption text-ink-muted uppercase">
                   <span>Current: {item.currentVal}</span>
                   <span className="text-ledger font-medium">Rec: {item.recommendedVal}</span>
                 </div>
                 <div className="h-2 bg-hairline rounded-full relative w-full">
                   <div className="absolute top-0 bottom-0 left-0 bg-paper rounded-full w-full" />
                   <div className="absolute top-1/2 -translate-y-1/2 -ml-1 w-2 h-4 bg-ink rounded-sm" style={{ left: `${item.currentVal * 100}%` }} />
                   <div className="absolute top-1/2 -translate-y-1/2 -ml-1 w-2 h-4 bg-ledger rounded-sm" style={{ left: `${item.recommendedVal * 100}%` }} />
                 </div>
               </div>

               <div className="bg-paper p-3 rounded text-body text-ink font-mono text-sm border border-hairline">
                 estimated_recall_impact: "{item.recallImpact}"
               </div>

               <div className="flex gap-3">
                  <Button variant="primary" className="flex-1" onClick={() => resolveCalibration(item.id, 'approve')}>Approve</Button>
                  <Button variant="ghost" className="flex-1" onClick={() => resolveCalibration(item.id, 'dismiss')}>Dismiss</Button>
               </div>
            </div>
          ))}
          {calibrationItems.length === 0 && (
            <div className="col-span-full p-8 text-center text-ink-muted border border-hairline bg-white rounded-lg italic">
              No threshold calibration recommendations available.
            </div>
          )}
        </div>
      )}

    </div>
  );
}

function QuarantineList<T extends { id: string }>({
  items,
  empty,
  render,
}: {
  items: T[];
  empty: string;
  render: (item: T) => React.ReactNode;
}) {
  if (items.length === 0) {
    return (
      <div className="p-8 text-center text-ink-muted border border-hairline bg-white rounded-lg italic">
        {empty}
      </div>
    );
  }

  return (
    <div className="border border-hairline bg-white rounded-lg divide-y divide-hairline overflow-hidden">
      {items.map(item => (
        <div key={item.id} className="p-4">
          {render(item)}
        </div>
      ))}
    </div>
  );
}
