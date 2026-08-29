import { useRef, useEffect, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { forceCollide, forceRadial } from 'd3-force-3d';

const NODE_COLORS: Record<string, string> = {
  Employee: '#0f766e',
  Organization: '#1d4ed8',
  Product: '#b45309',
  Repository: '#7c3aed',
  Document: '#64748b',
  GitRepository: '#7c3aed',
  GitFile: '#0ea5e9',
  GitPullRequest: '#b45309',
  GitIssue: '#a23b2e',
  GitMerge: '#6366f1',
  Developer: '#0f766e',
  CodeClass: '#8b5cf6',
  Entity: '#334155',
};

import { getNodeColor, type GraphNode, type GraphLink } from '../../screens/ViewerScreen';

interface Props {
  nodes: GraphNode[];
  edges: GraphLink[];
  width?: string | number;
  height?: string | number;
}

export function GraphPreview({ nodes, edges, width = '100%', height = '100%' }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<any>(null);
  const [dim, setDim] = useState({ width: 600, height: 400 });

  useEffect(() => {
    if (containerRef.current) {
      setDim({ width: containerRef.current.clientWidth, height: containerRef.current.clientHeight });
    }
    
    const handleResize = () => {
      if (containerRef.current) {
        setDim({ width: containerRef.current.clientWidth, height: containerRef.current.clientHeight });
      }
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    const fg = graphRef.current;
    if (!fg || !nodes.length) return;
    
    const charge = fg.d3Force('charge');
    charge?.strength?.(-600);
    charge?.distanceMax?.(800);

    const link = fg.d3Force('link');
    link?.distance?.(180);
    link?.strength?.(0.15);

    fg.d3Force('collide', forceCollide(() => 40).strength(0.8).iterations(3));
    
    const hub = nodes.find(n => n.type === 'Repository') || [...nodes].sort((a, b) => (b.val || 1) - (a.val || 1))[0];
    if (hub) {
      fg.d3Force('radial', forceRadial((n: GraphNode) => n.id === hub.id ? 0 : 200, 0, 0).strength(0.3));
    }
    fg.d3ReheatSimulation();
  }, [nodes, edges]);

  const nodeColor = (node: GraphNode) => getNodeColor(node);

  return (
    <div ref={containerRef} className="w-full h-full bg-[#fcfcfb] relative" style={{ width, height }}>
      {nodes.length > 0 ? (
        <>
          <div className="absolute top-4 left-4 z-10 bg-white/90 px-3 py-2 rounded-md shadow-sm border border-hairline text-sm font-mono text-ink">
            {nodes.length} Nodes | {edges.length} Edges
          </div>
          <ForceGraph2D
            ref={graphRef}
            width={dim.width}
            height={dim.height}
            graphData={{ nodes, links: edges }}
            nodeLabel={(n: GraphNode) => `${n.name}${n.type ? ` · ${n.type}` : ''}`}
            linkLabel={(l: GraphLink) => {
              const s = typeof l.source === 'object' ? l.source.name : l.source;
              const t = typeof l.target === 'object' ? l.target.name : l.target;
              const rel = (l.relation_type || 'RELATES_TO').replace(/_/g, ' ');
              return `${s} —[${rel}]→ ${t}`;
            }}
            nodeCanvasObject={(node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
              const size = Math.max(6, Math.sqrt(node.val || 1) * 3);
              const color = nodeColor(node);
              ctx.globalAlpha = 1;
              ctx.beginPath();
              ctx.arc(node.x || 0, node.y || 0, size, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.fill();
              ctx.strokeStyle = 'rgba(255,255,255,0.9)';
              ctx.lineWidth = 1.5 / globalScale;
              ctx.stroke();
              const showLabel = true;
              if (showLabel && node.name) {
                ctx.font = `500 ${Math.max(10, 11 / globalScale)}px ui-sans-serif`;
                ctx.fillStyle = '#1B1C1E';
                ctx.textAlign = 'center';
                const nameStr = String(node.name);
                ctx.fillText(nameStr.length > 20 ? nameStr.slice(0, 18) + '…' : nameStr, node.x || 0, (node.y || 0) + size + 14);
              }
            }}
            linkColor={(l: GraphLink) => {
              const rel = (l.relation_type || '').toLowerCase();
              if (rel.includes('use')) return '#0d9488';
              if (rel.includes('depend')) return '#b45309';
              if (rel.includes('contain')) return '#7c3aed';
              return '#475569'; // Default edge color
            }}
            linkWidth={(l: GraphLink) => {
              const rel = (l.relation_type || '').toLowerCase();
              return rel.includes('use') ? 1.5 : rel.includes('depend') ? 2 : 2.5;
            }}
            linkDirectionalArrowLength={6}
            linkDirectionalArrowRelPos={0.9}
            d3AlphaDecay={0.022}
            d3VelocityDecay={0.3}
            cooldownTicks={150}
            backgroundColor="transparent"
            onEngineStop={() => graphRef.current?.zoomToFit(300, 50)}
          />
        </>
      ) : (
        <div className="flex items-center justify-center h-full text-ink-muted italic">
          No community data to display
        </div>
      )}
    </div>
  );
}
