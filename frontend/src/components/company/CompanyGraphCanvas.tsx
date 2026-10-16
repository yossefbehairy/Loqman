import React, { useRef, useEffect, useMemo, useState, useCallback } from 'react';
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d';
import type { Agent, Team, CameraState, GraphOptions } from '../../types/agentos';
import {
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Maximize2,
  Sliders,
  Sparkles,
  Compass,
  X,
  Play,
  Flame,
} from 'lucide-react';

interface Props {
  agents: Agent[];
  teams: Team[];
  selectedId: string | null;
  focusTeamId: string | null;
  opts: GraphOptions;
  camera: CameraState;
  onCameraChange: (cam: CameraState) => void;
  onSelectAgent: (id: string | null) => void;
  onToggleStream?: () => void;
}

interface GraphAgentNode {
  id: string;
  agent: Agent;
  name: string;
  role: string;
  teamId: string | null;
  team?: Team;
  mgr: boolean;
  isCeo: boolean;
  status: Agent['status'];
  color: string;
  baseRadius: number;
  neighbors: Set<string>;
  links: GraphAgentLink[];
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
}

interface GraphAgentLink {
  id: string;
  source: string | GraphAgentNode;
  target: string | GraphAgentNode;
  teamColor: string;
  isCeoLink: boolean;
}

// Background subtle cosmic starfield coordinates
const STARFIELD_STARS = Array.from({ length: 60 }, (_, i) => ({
  x: ((i * 137.5) % 4000) - 2000,
  y: ((i * 223.7) % 4000) - 2000,
  r: 0.6 + (i % 3) * 0.4,
  alpha: 0.12 + (i % 5) * 0.05,
}));

export const CompanyGraphCanvas: React.FC<Props> = ({
  agents,
  teams,
  selectedId,
  focusTeamId,
  opts,
  camera,
  onCameraChange,
  onSelectAgent,
  onToggleStream,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<ForceGraphMethods<GraphAgentNode, GraphAgentLink> | undefined>(undefined);
  const lastCameraEmitRef = useRef(0);
  const lastAppliedCameraRef = useRef<CameraState | null>(null);

  const [dimensions, setDimensions] = useState<{ width: number; height: number }>({
    width: 800,
    height: 520,
  });

  // Hover and Spotlight state
  const [hoveredNode, setHoveredNode] = useState<GraphAgentNode | null>(null);
  const [isPhysicsDrawerOpen, setIsPhysicsDrawerOpen] = useState(false);

  // Obsidian-style Physics configuration state
  const [repulsionStrength, setRepulsionStrength] = useState<number>(-280);
  const [linkDistance, setLinkDistance] = useState<number>(85);
  const [centerGravity, setCenterGravity] = useState<number>(0.12);
  const [particleSpeed, setParticleSpeed] = useState<number>(0.005);
  const [showAlwaysLabels, setShowAlwaysLabels] = useState<boolean>(true);

  // Track container dimensions with ResizeObserver
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          setDimensions({
            width: Math.floor(width),
            height: Math.floor(height),
          });
        }
      }
    });

    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Compute Obsidian Graph Data
  const graphData = useMemo(() => {
    const nodes: GraphAgentNode[] = [];
    const links: GraphAgentLink[] = [];
    const nodeMap = new Map<string, GraphAgentNode>();

    const ceo = agents.find((a) => a.mgr && !a.team);

    agents.forEach((a) => {
      const isCeo = a.mgr && !a.team;
      const team = teams.find((t) => t.id === a.team);
      const color = isCeo ? '#fafafa' : team ? team.color : '#fafafa';
      const baseRadius = isCeo ? 18 : a.mgr ? 13 : 8;

      const node: GraphAgentNode = {
        id: a.id,
        agent: a,
        name: a.name,
        role: a.role,
        teamId: a.team,
        team,
        mgr: a.mgr,
        isCeo,
        status: a.status,
        color,
        baseRadius,
        neighbors: new Set<string>(),
        links: [],
      };
      nodes.push(node);
      nodeMap.set(a.id, node);
    });

    // 1. Link CEO to Department Managers
    if (ceo) {
      teams.forEach((t) => {
        if (t.mgr && nodeMap.has(t.mgr) && t.mgr !== ceo.id) {
          links.push({
            id: `${ceo.id}->${t.mgr}`,
            source: ceo.id,
            target: t.mgr,
            teamColor: t.color,
            isCeoLink: true,
          });
        }
      });
    }

    // 2. Link Department Managers to their Specialists
    teams.forEach((t) => {
      const managerId = t.mgr;
      if (!managerId || !nodeMap.has(managerId)) return;

      agents
        .filter((a) => a.team === t.id && a.id !== managerId)
        .forEach((member) => {
          links.push({
            id: `${managerId}->${member.id}`,
            source: managerId,
            target: member.id,
            teamColor: t.color,
            isCeoLink: false,
          });
        });
    });

    // Populate neighbors and references
    links.forEach((l) => {
      const sId = typeof l.source === 'object' ? (l.source as any).id : l.source;
      const tId = typeof l.target === 'object' ? (l.target as any).id : l.target;
      const sNode = nodeMap.get(sId);
      const tNode = nodeMap.get(tId);
      if (sNode && tNode) {
        sNode.neighbors.add(tId);
        tNode.neighbors.add(sId);
        sNode.links.push(l);
        tNode.links.push(l);
      }
    });

    return { nodes, links, nodeMap };
  }, [agents, teams]);

  // Determine active spotlight focus node ID (Obsidian spotlight effect)
  const activeFocusId = useMemo(() => {
    if (hoveredNode) return hoveredNode.id;
    if (selectedId) return selectedId;
    return null;
  }, [hoveredNode, selectedId]);

  // Configure d3-force physics simulation engine
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;

    try {
      const chargeForce = fg.d3Force('charge') as any;
      if (chargeForce && typeof chargeForce.strength === 'function') {
        chargeForce.strength(repulsionStrength);
        if (typeof chargeForce.distanceMax === 'function') {
          chargeForce.distanceMax(800);
        }
      }

      const linkForce = fg.d3Force('link') as any;
      if (linkForce && typeof linkForce.distance === 'function') {
        linkForce.distance((link: any) => {
          return link.isCeoLink ? linkDistance * 1.55 : linkDistance;
        });
        if (typeof linkForce.strength === 'function') {
          linkForce.strength(0.75);
        }
      }

      const centerForce = fg.d3Force('center') as any;
      if (centerForce && typeof centerForce.strength === 'function') {
        centerForce.strength(centerGravity);
      }

      fg.d3ReheatSimulation();
    } catch {
      // Safe fallback if force API variation
    }
  }, [repulsionStrength, linkDistance, centerGravity]);

  // Initial auto-fit. With 1-2 nodes zoomToFit would blow a single node up to
  // fill the canvas, so center those at a fixed zoom instead.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!fgRef.current) return;
      try {
        if (graphData.nodes.length <= 2) {
          fgRef.current.centerAt(0, 0, 400);
          fgRef.current.zoom(1, 400);
        } else {
          fgRef.current.zoomToFit(700, 50);
        }
      } catch {
        /* ignore */
      }
    }, 450);
    return () => clearTimeout(timer);
  }, [graphData]);

  // Pre-render Background: Subtle Obsidian grid and ambient starfield
  const handleRenderFramePre = useCallback(
    (ctx: CanvasRenderingContext2D, globalScale: number) => {
      ctx.save();
      for (let i = 0; i < STARFIELD_STARS.length; i++) {
        const star = STARFIELD_STARS[i];
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.r / Math.sqrt(globalScale), 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${star.alpha * 0.35})`;
        ctx.fill();
      }

      // Draw subtle orbital rings around origin
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
      ctx.lineWidth = 1 / globalScale;
      ctx.beginPath();
      ctx.arc(0, 0, 160, 0, Math.PI * 2);
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(0, 0, 310, 0, Math.PI * 2);
      ctx.stroke();

      ctx.restore();
    },
    []
  );

  // Custom Link Rendering: Organic glowing links with spotlight attenuation
  const handleLinkCanvasObject = useCallback(
    (link: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const s = typeof link.source === 'object' ? link.source : null;
      const t = typeof link.target === 'object' ? link.target : null;
      if (!s || !t || s.x === undefined || s.y === undefined || t.x === undefined || t.y === undefined) return;

      const sId = s.id;
      const tId = t.id;

      const isConnectedToFocus = activeFocusId
        ? sId === activeFocusId || tId === activeFocusId
        : focusTeamId
        ? (s.teamId === focusTeamId || s.isCeo) && (t.teamId === focusTeamId || t.isCeo)
        : true;

      ctx.save();
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(t.x, t.y);

      if (isConnectedToFocus) {
        const highlightColor = activeFocusId ? '#fafafa' : link.teamColor || '#a1a1aa';
        ctx.strokeStyle = highlightColor;
        ctx.lineWidth = (activeFocusId ? 2.4 : 1.6) / Math.sqrt(globalScale);
        ctx.globalAlpha = 0.9;
        if (activeFocusId || opts.trails) {
          ctx.shadowColor = highlightColor;
          ctx.shadowBlur = 10;
        }
      } else {
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.16)';
        ctx.lineWidth = 1.0 / Math.sqrt(globalScale);
        ctx.globalAlpha = 0.08;
      }

      ctx.stroke();
      ctx.restore();
    },
    [activeFocusId, focusTeamId, opts.trails]
  );

  // Custom Node Rendering: Multi-layered Obsidian aesthetic with status rings and labels
  const handleNodeCanvasObject = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const gNode = node as GraphAgentNode;
      const isConnectedToFocus = activeFocusId
        ? gNode.id === activeFocusId || gNode.neighbors.has(activeFocusId)
        : focusTeamId
        ? gNode.isCeo || gNode.teamId === focusTeamId
        : true;

      const isHovered = hoveredNode?.id === gNode.id;
      const isSelected = selectedId === gNode.id;

      ctx.save();
      ctx.globalAlpha = isConnectedToFocus ? 1 : 0.16;

      const x = gNode.x || 0;
      const y = gNode.y || 0;
      const baseR = gNode.baseRadius;

      // Smooth pulsing rhythm for active nodes
      const pulse =
        opts.motion && gNode.status === 'Active'
          ? Math.sin(Date.now() / 320 + (gNode.x || 0)) * 1.5
          : 0;
      const r = Math.max(4, baseR + (isHovered || isSelected ? 3 : 0) + pulse);

      // 1. Ambient Glow Halo
      if (isConnectedToFocus || isHovered || isSelected) {
        ctx.beginPath();
        ctx.arc(x, y, r + 5, 0, Math.PI * 2);
        ctx.fillStyle = gNode.color;
        ctx.shadowColor = gNode.color;
        ctx.shadowBlur = isHovered || isSelected ? 28 : 14;
        ctx.globalAlpha = isConnectedToFocus ? (isHovered || isSelected ? 0.38 : 0.22) : 0.05;
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.globalAlpha = isConnectedToFocus ? 1 : 0.16;
      }

      // 2. Main Node Body
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = gNode.color;
      ctx.fill();

      // 3. Central Core for CEO and Managers (Obsidian planetary style)
      if (gNode.isCeo || gNode.mgr) {
        ctx.beginPath();
        ctx.arc(x, y, r * 0.56, 0, Math.PI * 2);
        ctx.fillStyle = '#121214';
        ctx.fill();

        // Inner pip
        ctx.beginPath();
        ctx.arc(x, y, r * 0.26, 0, Math.PI * 2);
        ctx.fillStyle = gNode.color;
        ctx.fill();
      }

      // 4. Status Attention Ring
      if (gNode.status === 'Attention') {
        ctx.beginPath();
        ctx.arc(x, y, r + 4.5, 0, Math.PI * 2);
        ctx.strokeStyle = '#fafafa';
        ctx.lineWidth = 1.8 / Math.sqrt(globalScale);
        ctx.setLineDash([4 / Math.sqrt(globalScale), 3 / Math.sqrt(globalScale)]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // 5. Selected Highlight Aura Ring
      if (isSelected) {
        ctx.beginPath();
        ctx.arc(x, y, r + 7, 0, Math.PI * 2);
        ctx.strokeStyle = gNode.color;
        ctx.lineWidth = 2.2 / Math.sqrt(globalScale);
        ctx.stroke();
      }

      // 6. Adaptive Typography Labels (Obsidian LOD)
      const shouldRenderLabel =
        showAlwaysLabels ||
        isHovered ||
        isSelected ||
        globalScale > 0.95 ||
        gNode.isCeo ||
        gNode.mgr;

      if (shouldRenderLabel) {
        const fontSize = Math.max(10, Math.min(14, 12 / Math.sqrt(globalScale)));
        ctx.font = `${gNode.mgr || gNode.isCeo ? '600' : '500'} ${fontSize}px Inter, -apple-system, BlinkMacSystemFont, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';

        const labelY = y + r + 5;
        const text = gNode.name;

        // Shadow outline for text readability
        ctx.fillStyle = '#121214';
        ctx.fillText(text, x - 1, labelY);
        ctx.fillText(text, x + 1, labelY);
        ctx.fillText(text, x, labelY - 1);
        ctx.fillText(text, x, labelY + 1);

        ctx.fillStyle = isConnectedToFocus ? '#fafafa' : '#a1a1aa';
        ctx.fillText(text, x, labelY);

        // Department / Role badge
        if (globalScale > 0.85 || isHovered || isSelected || gNode.mgr) {
          const subSize = Math.max(8.5, fontSize * 0.8);
          ctx.font = `500 ${subSize}px Inter, sans-serif`;
          ctx.fillStyle = gNode.team ? gNode.team.color : '#a1a1aa';
          const subText = gNode.isCeo ? 'EXECUTIVE' : gNode.role;
          ctx.fillText(subText, x, labelY + fontSize + 2);
        }
      }

      ctx.restore();
    },
    [activeFocusId, focusTeamId, hoveredNode, selectedId, opts.motion, showAlwaysLabels]
  );

  // Apply external camera resets (e.g. Options > Reset camera) to the canvas.
  // Guarded by ref so canvas-driven onZoom events don't loop back.
  useEffect(() => {
    if (!fgRef.current || !camera) return;
    const last = lastAppliedCameraRef.current;
    if (last && last.x === camera.x && last.y === camera.y && last.k === camera.k) return;
    // Only apply explicit resets centered at origin to avoid fighting user pan/zoom
    if (camera.x === 0 && camera.y === 0 && (camera.k === 0.95 || camera.k === 1)) {
      lastAppliedCameraRef.current = camera;
      try {
        fgRef.current.centerAt(0, 0, 400);
        fgRef.current.zoom(camera.k, 400);
      } catch {
        /* ignore */
      }
    }
  }, [camera]);

  // Zoom and Camera Controls
  const handleZoomIn = () => {
    if (fgRef.current) {
      const currentZoom = fgRef.current.zoom();
      fgRef.current.zoom(Math.min(currentZoom * 1.35, 6), 350);
    }
  };

  const handleZoomOut = () => {
    if (fgRef.current) {
      const currentZoom = fgRef.current.zoom();
      fgRef.current.zoom(Math.max(currentZoom * 0.72, 0.25), 350);
    }
  };

  const handleResetCamera = () => {
    if (fgRef.current) {
      fgRef.current.centerAt(0, 0, 500);
      fgRef.current.zoomToFit(600, 50);
    }
    onCameraChange({ x: 0, y: 0, k: 0.95 });
  };

  const handleReheatPhysics = () => {
    if (fgRef.current) {
      fgRef.current.d3ReheatSimulation();
    }
  };

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden select-none bg-[#121214]"
      style={{ minHeight: '480px' }}
    >
      {/* 2D Canvas Force Graph */}
      <ForceGraph2D
        ref={fgRef as any}
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData as any}
        nodeId="id"
        nodeRelSize={1}
        nodeVal={(n: any) => n.baseRadius}
        onRenderFramePre={handleRenderFramePre}
        nodeCanvasObject={handleNodeCanvasObject}
        nodeCanvasObjectMode={() => 'replace'}
        linkCanvasObject={handleLinkCanvasObject}
        linkCanvasObjectMode={() => 'replace'}
        // Directional Particles (neural transmission flow)
        linkDirectionalParticles={opts.motion ? (link: any) => (link.isCeoLink ? 3 : 2) : 0}
        linkDirectionalParticleSpeed={particleSpeed}
        linkDirectionalParticleWidth={opts.trails ? 3 : 2}
        linkDirectionalParticleColor={(link: any) => link.teamColor || '#fafafa'}
        // Obsidian Physics Damping Parameters
        d3AlphaDecay={0.016}
        d3VelocityDecay={0.34}
        warmupTicks={30}
        cooldownTicks={120}
        // Interactions
        enableNodeDrag={true}
        onNodeDragEnd={(node: any) => {
          // Release fixed coordinates so node gently relaxes back into equilibrium
          node.fx = undefined;
          node.fy = undefined;
          if (fgRef.current) {
            fgRef.current.d3ReheatSimulation();
          }
        }}
        onNodeClick={(node: any) => {
          onSelectAgent(node.id === selectedId ? null : node.id);
        }}
        onNodeHover={(node: any) => {
          setHoveredNode(node ? (node as GraphAgentNode) : null);
        }}
        onBackgroundClick={() => {
          onSelectAgent(null);
          setHoveredNode(null);
        }}
        onZoom={(transform) => {
          // Throttle: canvas fires every frame during zoom — cap parent state updates
          const now = Date.now();
          if (now - lastCameraEmitRef.current < 200) return;
          lastCameraEmitRef.current = now;
          lastAppliedCameraRef.current = { x: transform.x, y: transform.y, k: transform.k };
          onCameraChange({ x: transform.x, y: transform.y, k: transform.k });
        }}
      />

      {/* Obsidian Canvas HUD Controls */}
      <div className="absolute left-4 bottom-4 flex items-center gap-1.5 p-1 bg-[var(--card2)]/90 backdrop-blur-xl border border-[var(--line2)] rounded-xl shadow-2xl z-20">
        <button
          onClick={handleZoomIn}
          className="p-1.5 text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5 rounded-lg transition-colors"
          title="Zoom In (+)"
          aria-label="Zoom in"
        >
          <ZoomIn size={16} />
        </button>
        <button
          onClick={handleZoomOut}
          className="p-1.5 text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5 rounded-lg transition-colors"
          title="Zoom Out (-)"
          aria-label="Zoom out"
        >
          <ZoomOut size={16} />
        </button>

        <div className="w-[1px] h-4 bg-[var(--line)]" />

        <button
          onClick={handleResetCamera}
          className="p-1.5 text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5 rounded-lg transition-colors"
          title="Fit to screen & center (Obsidian Reset)"
          aria-label="Reset camera"
        >
          <RotateCcw size={15} />
        </button>

        <button
          onClick={handleReheatPhysics}
          className="p-1.5 text-[var(--muted)] hover:text-[var(--acc)] hover:bg-white/5 rounded-lg transition-colors"
          title="Simulate / Reheat Physics (حركة فيزيائية)"
          aria-label="Reheat simulation"
        >
          <Flame size={15} />
        </button>

        <div className="w-[1px] h-4 bg-[var(--line)]" />

        {/* Obsidian Physics Tuning Sliders Toggle */}
        <button
          onClick={() => setIsPhysicsDrawerOpen((prev) => !prev)}
          className={`p-1.5 rounded-lg transition-colors flex items-center gap-1.5 text-xs font-medium ${
            isPhysicsDrawerOpen
              ? 'bg-[var(--acc)]/20 text-[var(--acc)] border border-[var(--acc)]/40'
              : 'text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5'
          }`}
          title="Obsidian Physics Controls"
          aria-label="Physics controls"
        >
          <Sliders size={15} />
          <span className="hidden sm:inline pr-1">Forces</span>
        </button>

        {onToggleStream && (
          <>
            <div className="w-[1px] h-4 bg-[var(--line)]" />
            <button
              onClick={onToggleStream}
              className="p-1.5 text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5 rounded-lg transition-colors"
              title="Stream mode (F)"
              aria-label="Stream mode"
            >
              <Maximize2 size={15} />
            </button>
          </>
        )}
      </div>

      {/* Obsidian Graph Physics Settings Modal / Floating Panel */}
      {isPhysicsDrawerOpen && (
        <div className="absolute left-4 bottom-16 w-72 p-4 bg-[var(--card)]/95 backdrop-blur-2xl border border-[var(--line-strong)] rounded-2xl shadow-2xl z-30 flex flex-col gap-3 text-xs animate-in fade-in slide-in-from-bottom-2 duration-150">
          <div className="flex items-center justify-between pb-2 border-b border-[var(--line)]">
            <span className="font-semibold text-[var(--text)] flex items-center gap-1.5">
              <Compass size={14} className="text-[var(--acc)]" />
              Obsidian Physics (Forces)
            </span>
            <button
              onClick={() => setIsPhysicsDrawerOpen(false)}
              className="text-[var(--muted)] hover:text-[var(--text)] p-1 rounded-md hover:bg-white/5"
            >
              <X size={14} />
            </button>
          </div>

          {/* Repulsion Force */}
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between text-[11px]">
              <span className="text-[var(--muted)]">Repulsion (قوة التنافر)</span>
              <span className="mono text-[var(--acc)]">{Math.abs(repulsionStrength)}</span>
            </div>
            <input
              type="range"
              min="-600"
              max="-100"
              step="20"
              value={repulsionStrength}
              onChange={(e) => setRepulsionStrength(Number(e.target.value))}
              className="w-full accent-[var(--acc)] cursor-pointer"
            />
          </div>

          {/* Link Distance */}
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between text-[11px]">
              <span className="text-[var(--muted)]">Link Distance (طول الروابط)</span>
              <span className="mono text-[var(--acc)]">{linkDistance}px</span>
            </div>
            <input
              type="range"
              min="35"
              max="200"
              step="5"
              value={linkDistance}
              onChange={(e) => setLinkDistance(Number(e.target.value))}
              className="w-full accent-[var(--acc)] cursor-pointer"
            />
          </div>

          {/* Center Gravity */}
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between text-[11px]">
              <span className="text-[var(--muted)]">Center Gravity (الجاذبية)</span>
              <span className="mono text-[var(--acc)]">{(centerGravity * 100).toFixed(0)}%</span>
            </div>
            <input
              type="range"
              min="0.02"
              max="0.4"
              step="0.02"
              value={centerGravity}
              onChange={(e) => setCenterGravity(Number(e.target.value))}
              className="w-full accent-[var(--acc)] cursor-pointer"
            />
          </div>

          {/* Particle Flow Speed */}
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between text-[11px]">
              <span className="text-[var(--muted)]">Neural Pulse Speed</span>
              <span className="mono text-[var(--acc)]">{(particleSpeed * 1000).toFixed(0)}x</span>
            </div>
            <input
              type="range"
              min="0.001"
              max="0.015"
              step="0.001"
              value={particleSpeed}
              onChange={(e) => setParticleSpeed(Number(e.target.value))}
              className="w-full accent-[var(--acc)] cursor-pointer"
            />
          </div>

          {/* Labels Toggle */}
          <div className="flex items-center justify-between pt-1 border-t border-[var(--line)]">
            <span className="text-[11px] text-[var(--muted)]">Always Show Labels</span>
            <input
              type="checkbox"
              checked={showAlwaysLabels}
              onChange={(e) => setShowAlwaysLabels(e.target.checked)}
              className="rounded accent-[var(--acc)] cursor-pointer"
            />
          </div>

          <button
            onClick={handleReheatPhysics}
            className="os-btn os-btn-sm w-full mt-1 flex items-center justify-center gap-1.5"
          >
            <Play size={12} />
            Reheat Simulation
          </button>
        </div>
      )}

      {/* Obsidian-style Bottom Status Indicator */}
      <div className="absolute bottom-3 left-0 right-0 text-center text-xs text-[var(--dim)] pointer-events-none flex items-center justify-center gap-2">
        <Sparkles size={12} className="text-[var(--acc)] animate-pulse" />
        <span>Obsidian Physics Graph · Drag nodes to stretch · Scroll to zoom · Hover to spotlight</span>
      </div>
    </div>
  );
};
