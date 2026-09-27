import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../api";
import { SUGGESTIONS_KEY } from "../hooks";
import { dateRange, layerTasks } from "../lib";
import { COLUMNS, type Board, type Task } from "../types";

const COL_W = 250;
const ROW_H = 118;

type TaskNodeData = { task: Task; critical: boolean };

function TaskNode({ data }: NodeProps<Node<TaskNodeData>>) {
  const { task, critical } = data;
  const status = COLUMNS.find((c) => c.status === task.status)?.label;
  return (
    <div className={`gnode gnode--${task.is_blocked ? "blocked" : "ready"} ${critical ? "gnode--critical" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <p className="gnode__status">
        {status} · {task.is_blocked ? "Blocked" : "Ready"}
      </p>
      <p className="gnode__title">{task.title}</p>
      <p className="gnode__dates mono">
        {dateRange(task.start_date, task.end_date)} · {task.duration_days}d
      </p>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { task: TaskNode };

export function GraphView({ board, onOpen, showCritical }: { board: Board; onOpen: (id: number) => void; showCritical: boolean }) {
  const suggestions = useQuery({ queryKey: SUGGESTIONS_KEY, queryFn: api.pendingSuggestions });

  const { nodes, edges } = useMemo(() => {
    const layers = layerTasks(board.tasks);
    const chain = board.critical_chain;
    const criticalEdges = new Set(chain.slice(1).map((id, i) => `${chain[i]}-${id}`));
    const onChain = new Set(chain);

    const nodes: Node<TaskNodeData>[] = board.tasks.map((t) => {
      const pos = layers.get(t.id)!;
      return {
        id: String(t.id),
        type: "task",
        position: { x: pos.layer * COL_W, y: pos.row * ROW_H },
        data: { task: t, critical: showCritical && onChain.has(t.id) },
      };
    });

    const edges: Edge[] = board.dependencies.map((d) => {
      const critical = showCritical && criticalEdges.has(`${d.predecessor_id}-${d.successor_id}`);
      return {
        id: `d${d.id}`,
        source: String(d.predecessor_id),
        target: String(d.successor_id),
        className: critical ? "gedge--critical" : d.source === "ai" ? "gedge--ai" : "",
        markerEnd: { type: MarkerType.ArrowClosed },
      };
    });

    for (const s of suggestions.data ?? []) {
      edges.push({
        id: `s${s.id}`,
        source: String(s.predecessor_id),
        target: String(s.successor_id),
        className: "gedge--pending",
        animated: true,
        label: "suggested",
        markerEnd: { type: MarkerType.ArrowClosed },
      });
    }
    return { nodes, edges };
  }, [board, suggestions.data, showCritical]);

  return (
    <div className="graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12, minZoom: 0.75 }}
        nodesDraggable={false}
        nodesConnectable={false}
        onNodeClick={(_, n) => onOpen(Number(n.id))}
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      <div className="graph__legend">
        <span><i className="lg lg--ready" /> Ready</span>
        <span><i className="lg lg--blocked" /> Blocked</span>
        {showCritical && <span><i className="lg lg--critical" /> Critical path</span>}
        <span><i className="lg lg--pending" /> Suggested (not saved)</span>
      </div>
    </div>
  );
}
