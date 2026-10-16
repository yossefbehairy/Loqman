import React, { useCallback, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  BackgroundVariant,
} from '@xyflow/react';
import type {
  Node,
  Edge,
  Connection,
  NodeChange,
  EdgeChange,
} from '@xyflow/react';
import { Play, Bot, Flag, AlertTriangle } from 'lucide-react';
import { StartNode } from './nodes/StartNode';
import { AgentNode } from './nodes/AgentNode';
import { OutputNode } from './nodes/OutputNode';
import { DepartmentZoneCard } from '../map/DepartmentZone';
import { ContractCardModal } from './ContractCardModal';

interface WorkflowCanvasProps {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;
  onSelectNode: (node: Node | null) => void;
  onAddNode: (type: 'start' | 'agent' | 'output') => void;
  validationErrors?: string[];
  isRunning?: boolean;
}

export const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onSelectNode,
  onAddNode,
  validationErrors = [],
  isRunning = false,
}) => {
  const [contractModalNode, setContractModalNode] = useState<Node | null>(null);

  const nodeTypes = useMemo(
    () => ({
      start: StartNode,
      agent: AgentNode,
      output: OutputNode,
    }),
    []
  );

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onSelectNode(node);
      if (node.type === 'agent') {
        setContractModalNode(node);
      }
    },
    [onSelectNode]
  );

  const handlePaneClick = useCallback(() => {
    onSelectNode(null);
    setContractModalNode(null);
  }, [onSelectNode]);

  // Always resolve the freshest data for the active node in ContractCardModal
  const activeAgentNode = contractModalNode
    ? nodes.find((n) => n.id === contractModalNode.id) || contractModalNode
    : null;

  return (
    <div
      data-testid="agent-map-canvas"
      className="interactive-map-container flex-1 h-full relative overflow-hidden bg-gray-950"
    >
      {/* Node Palette (Floating Top-Left) */}
      <div className="absolute top-3 left-4 z-10 bg-gray-900/90 backdrop-blur-md border border-gray-800 rounded-xl p-2 shadow-xl flex items-center space-x-1.5">
        <span className="text-[11px] font-bold text-gray-400 uppercase px-2">Add Node:</span>

        <button
          onClick={() => onAddNode('start')}
          className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-emerald-950/60 hover:bg-emerald-900/60 text-emerald-300 border border-emerald-800/80 text-xs font-semibold transition"
          title="Add Start Node"
        >
          <Play className="w-3.5 h-3.5 fill-emerald-400" />
          <span>Start</span>
        </button>

        <button
          onClick={() => onAddNode('agent')}
          className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-indigo-950/60 hover:bg-indigo-900/60 text-indigo-300 border border-indigo-800/80 text-xs font-semibold transition"
          title="Add AI Agent Node"
        >
          <Bot className="w-3.5 h-3.5 text-indigo-400" />
          <span>AI Agent</span>
        </button>

        <button
          onClick={() => onAddNode('output')}
          className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-purple-950/60 hover:bg-purple-900/60 text-purple-300 border border-purple-800/80 text-xs font-semibold transition"
          title="Add Output Node"
        >
          <Flag className="w-3.5 h-3.5 fill-purple-400" />
          <span>Output</span>
        </button>
      </div>

      {/* Department Zones Boundary Banner (R4: Visual Department Boundaries & Groupings) */}
      <div className="absolute top-16 left-4 right-4 z-10 pointer-events-none flex flex-wrap items-center gap-3">
        <div className="pointer-events-auto flex-1 min-w-[200px]">
          <DepartmentZoneCard
            id="planning"
            name="Planning & Strategy"
            nameArabic="إدارة التخطيط"
            managerArabic="د. سارة"
            managerEnglish="Dr. Sara"
            colorScheme="indigo"
            activeAgentsCount={
              nodes.filter((n) => (n.data as any)?.role_type === 'planner').length || 1
            }
          />
        </div>
        <div className="pointer-events-auto flex-1 min-w-[200px]">
          <DepartmentZoneCard
            id="research"
            name="Research & Feasibility"
            nameArabic="إدارة التحليل والمخاطر"
            managerArabic="م. طارق"
            managerEnglish="Eng. Tarek"
            colorScheme="amber"
            activeAgentsCount={
              nodes.filter((n) => (n.data as any)?.role_type === 'analyst').length || 1
            }
          />
        </div>
        <div className="pointer-events-auto flex-1 min-w-[200px]">
          <DepartmentZoneCard
            id="quality"
            name="Quality Assurance & Review"
            nameArabic="إدارة الجودة والاعتماد"
            managerArabic="أ. ياسمين"
            managerEnglish="Prof. Yasmin"
            colorScheme="emerald"
            activeAgentsCount={
              nodes.filter((n) => (n.data as any)?.role_type === 'reviewer').length || 1
            }
          />
        </div>
      </div>

      {/* Validation Errors Overlay */}
      {validationErrors && validationErrors.length > 0 && (
        <div className="absolute top-3 right-4 z-10 max-w-md bg-rose-950/95 border border-rose-800 text-rose-200 p-3.5 rounded-xl shadow-2xl space-y-1.5 backdrop-blur-md animate-in fade-in slide-in-from-top-2">
          <div className="flex items-center space-x-2 text-rose-400 font-bold text-xs">
            <AlertTriangle className="w-4 h-4 flex-shrink-0" />
            <span>DAG Graph Validation Issues ({validationErrors.length})</span>
          </div>
          <ul className="text-[11px] list-disc list-inside space-y-0.5 text-rose-300/90 max-h-32 overflow-y-auto">
            {validationErrors.map((err, idx) => (
              <li key={idx} className="leading-snug">
                {err}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* React Flow Canvas */}
      <ReactFlow
        nodes={nodes}
        edges={edges.map((e) => ({
          ...e,
          animated: isRunning,
          style: { stroke: isRunning ? '#72b4eb' : '#55aaf0', strokeWidth: 2.5 },
        }))}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={handleNodeClick}
        onPaneClick={handlePaneClick}
        nodeTypes={nodeTypes}
        fitView
        className="bg-gray-950"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="#383838" />
        <Controls className="!bg-gray-900 !border-gray-800 !rounded-xl !shadow-xl !fill-gray-300" />
        <MiniMap
          className="!bg-gray-900 !border-gray-800 !rounded-xl !overflow-hidden !shadow-xl"
          nodeColor={(n) => {
            if (n.type === 'start') return '#55aaf0';
            if (n.type === 'output') return '#55aaf0';
            return '#55aaf0';
          }}
          maskColor="rgba(37, 37, 37, 0.7)"
        />
      </ReactFlow>

      {/* R4: Contract Card & Output Preview Inspector Modal */}
      {activeAgentNode && (
        <ContractCardModal
          node={activeAgentNode}
          onClose={() => setContractModalNode(null)}
        />
      )}
    </div>
  );
};

export default WorkflowCanvas;
