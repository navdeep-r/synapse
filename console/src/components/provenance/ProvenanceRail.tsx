import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X } from 'lucide-react';
import { useProvenance } from './ProvenanceContext';

export function ProvenanceRail() {
  const { isOpen, data, closeProvenance } = useProvenance();
  const shouldReduceMotion = useReducedMotion();

  const lineDuration = shouldReduceMotion ? 0 : 0.32;
  const staggerDuration = shouldReduceMotion ? 0 : 0.04;
  const fadeDuration = shouldReduceMotion ? 0.1 : 0.2;

  const stubs = data ? [
    { label: 'Source Chunk Excerpt', value: `"${data.sourceChunkText}"`, highlight: true },
    { label: 'Source Document', value: data.sourceDocumentName },
    { label: 'Access Tag', value: data.accessTag },
    { label: 'Extraction Confidence', value: (data.extractionConfidence * 100).toFixed(1) + '%' },
    { label: 'Resolution Confidence', value: (data.resolutionConfidence * 100).toFixed(1) + '%' },
    { label: 'Mutation ID', value: data.mutationId },
    { label: 'Snapshot Version', value: data.snapshotVersion },
  ] : [];

  return (
    <AnimatePresence>
      {isOpen && data && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-40 bg-ink/20 backdrop-blur-sm lg:hidden"
            onClick={closeProvenance}
          />
          
          <motion.aside
            initial={{ x: '100%', opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: '100%', opacity: 0 }}
            transition={{ 
              type: shouldReduceMotion ? 'tween' : 'spring',
              duration: fadeDuration,
              bounce: 0 
            }}
            className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-[400px] border-l border-hairline bg-paper shadow-2xl lg:shadow-none"
          >
            <div className="flex h-full flex-col">
              <div className="flex items-center justify-between border-b border-hairline px-6 py-4">
                <h2 className="text-h2 text-ink">Provenance</h2>
                <button 
                  onClick={closeProvenance}
                  className="rounded-md p-2 text-ink-muted hover:bg-hairline hover:text-ink transition-colors"
                  aria-label="Close provenance rail"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto px-6 py-8 relative">
                <motion.div 
                  initial={{ height: 0 }}
                  animate={{ height: '100%' }}
                  transition={{ duration: lineDuration, ease: "linear" }}
                  className="absolute left-8 top-12 bottom-8 w-px bg-ledger/30 origin-top"
                />

                <div className="space-y-8 relative z-10">
                  {stubs.map((stub, idx) => (
                    <motion.div
                      key={idx}
                      initial={{ opacity: 0, y: shouldReduceMotion ? 0 : 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ 
                        delay: shouldReduceMotion ? 0 : lineDuration + (idx * staggerDuration),
                        duration: fadeDuration
                      }}
                      className="flex items-start gap-4"
                    >
                      <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-ledger ring-4 ring-paper" />
                      <div className="flex-1">
                        <div className="text-caption text-ink-muted uppercase tracking-wider mb-1">
                          {stub.label}
                        </div>
                        <div className={`font-mono text-body overflow-x-auto whitespace-nowrap scrollbar-thin ${stub.highlight ? 'text-ink italic bg-hairline/30 p-2 rounded' : 'text-ledger'}`}>
                          {stub.value}
                        </div>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </div>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
