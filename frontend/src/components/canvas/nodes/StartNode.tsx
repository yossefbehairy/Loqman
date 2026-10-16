import React from 'react';
import { Handle, Position } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';
import { Play, CheckCircle2, Loader2, Clock } from 'lucide-react';
import type { StartNodeData } from '../../../types';

export const StartNode: React.FC<NodeProps> = ({ data, selected }) => {
  const nodeData = data as unknown as StartNodeData;
  const status = nodeData.executionStatus || 'pending';

  return (
    <div
      className={`min-w-[220px] rounded-xl border bg-gray-900/95 backdrop-blur-md p-4 shadow-xl transition-all duration-200 ${
        selected ? 'ring-2 ring-emerald-400 border-emerald-400' : 'border-emerald-600/50'
      } ${
        status === 'running'
          ? 'ring-2 ring-blue-500 shadow-blue-500/20'
          : status === 'succeeded'
          ? 'border-emerald-500 shadow-emerald-500/10'
          : ''
      }`}
    >
      <div className="flex items-center justify-between pb-2 border-b border-gray-800">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 bg-emerald-500/20 rounded-lg text-emerald-400">
            <Play className="w-4 h-4 fill-emerald-400" />
          </div>
          <div>
            <span className="text-xs font-semibold text-emerald-400 tracking-wider uppercase">Trigger</span>
            <h4 className="text-sm font-bold text-gray-100">{nodeData.label || 'Workflow Start'}</h4>
          </div>
        </div>

        <div>
          {status === 'running' && <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />}
          {status === 'succeeded' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
          {status === 'pending' && <Clock className="w-4 h-4 text-gray-500" />}
        </div>
      </div>

      <div className="mt-3">
        <p className="text-xs text-gray-400 line-clamp-2">
          {nodeData.initialInput || 'Awaiting initial prompt payload...'}
        </p>
      </div>

      <div className="mt-2 pt-2 border-t border-gray-800/60 flex items-center justify-between">
        <span className="text-[10px] text-gray-500 font-mono">ID: start</span>
        <span
          className={`text-[10px] uppercase font-bold px-1.5 py-0.5 rounded ${
            status === 'succeeded'
              ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
              : status === 'running'
              ? 'bg-blue-950 text-blue-300 border border-blue-800 animate-pulse'
              : 'bg-gray-800 text-gray-400'
          }`}
        >
          {status}
        </span>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-3 !h-3 !bg-emerald-400 !border-2 !border-gray-900 shadow-md"
      />
    </div>
  );
};
