import * as React from 'react';

export interface ProvenanceData {
  factText?: string;
  sourceChunkText: string;
  sourceDocumentName: string;
  accessTag: string;
  extractionConfidence: number;
  resolutionConfidence: number;
  mutationId: string;
  snapshotVersion: string;
}

interface ProvenanceContextState {
  isOpen: boolean;
  data: ProvenanceData | null;
  openProvenance: (data: ProvenanceData) => void;
  closeProvenance: () => void;
}

const ProvenanceContext = React.createContext<ProvenanceContextState | undefined>(undefined);

export function ProvenanceProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setIsOpen] = React.useState(false);
  const [data, setData] = React.useState<ProvenanceData | null>(null);

  const openProvenance = React.useCallback((newData: ProvenanceData) => {
    setData(newData);
    setIsOpen(true);
  }, []);

  const closeProvenance = React.useCallback(() => {
    setIsOpen(false);
  }, []);

  return (
    <ProvenanceContext.Provider value={{ isOpen, data, openProvenance, closeProvenance }}>
      {children}
    </ProvenanceContext.Provider>
  );
}

export function useProvenance() {
  const context = React.useContext(ProvenanceContext);
  return context || {
    isOpen: false,
    data: null,
    openProvenance: () => {},
    closeProvenance: () => {}
  };
}
