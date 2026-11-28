import type { Agent, Team } from '../types';

export type ChartNode =
  | { kind: 'agent'; id: string; agent: Agent }
  | { kind: 'team'; id: string; name: string; members: Agent[] };

export function organizationNodes(agents: Agent[], teams: Team[]): ChartNode[] {
  const nodes: ChartNode[] = [];
  const grouped = new Map<string, Extract<ChartNode, { kind: 'team' }>>();
  const names = new Map(teams.map(team => [team.id.trim(), team.name.trim()]));
  for (const agent of agents) {
    if (agent.mgr) continue;
    const id = agent.team?.trim();
    if (!id || id === 'agents') {
      nodes.push({ kind: 'agent', id: agent.id, agent });
      continue;
    }
    let team = grouped.get(id);
    if (!team) {
      team = { kind: 'team', id, name: names.get(id) || id, members: [] };
      grouped.set(id, team);
      nodes.push(team);
    }
    team.members.push(agent);
  }
  return nodes.map(node => node.kind === 'team' && node.members.length === 1
    ? { kind: 'agent', id: node.members[0].id, agent: node.members[0] }
    : node);
}

interface Point { x: number; y: number }
interface Box extends Point { width: number; height: number }
const hubSize = { width: 208, height: 160 };
const gap = 26;

function slots(count: number): Point[] {
  if (count > 8) {
    // Stretch the ring vertically while keeping side cards within scrolling reach.
    const points = [{ x: 0, y: -1 }, { x: 0, y: 1 }];
    for (const side of [-1, 1]) {
      const rows = side < 0 ? Math.ceil((count - 2) / 2) : Math.floor((count - 2) / 2);
      for (let row = 0; row < rows; row++) {
        const y = 1.65 * (row / (rows - 1) - 0.5);
        points.push({ x: side * (1 - 0.25 * y * y), y });
      }
    }
    return points;
  }
  const rotation = count === 1 || count === 2 ? 0 : count % 2 ? Math.PI / 2 : Math.PI / count;
  return Array.from({ length: count }, (_, i) => {
    const angle = rotation + i * 2 * Math.PI / count;
    const x = Math.cos(angle);
    const y = Math.sin(angle);
    return {
      x: Math.abs(x) < 0.0001 ? 0 : Math.sign(x) * Math.abs(x) ** 0.62,
      y: Math.abs(y) < 0.0001 ? 0 : Math.sign(y) * Math.abs(y) ** 0.82,
    };
  });
}

function port(box: Box, target: Point, horizontal: boolean) {
  const dx = target.x - box.x;
  const dy = target.y - box.y;
  const norm = Math.max(Math.abs(dx / box.width), Math.abs(dy / box.height));
  const nx = horizontal ? Math.sign(dx) : 0;
  const ny = horizontal ? 0 : Math.sign(dy);
  // Spread adjacent lines along the facing edge, clear of the rounded corners.
  return {
    x: box.x + box.width * (nx * 0.5 + (horizontal ? 0 : 0.32 * dx / box.width / norm)),
    y: box.y + box.height * (ny * 0.5 + (horizontal ? 0.32 * dy / box.height / norm : 0)),
    nx, ny,
  };
}

export function chartLayout(nodes: ChartNode[], availableWidth: number, availableHeight: number) {
  const teamWidth = Math.min(344, Math.max(244, (availableWidth - hubSize.width - 96) / 2));
  const positions = slots(nodes.length).sort((a, b) => Math.round(a.y * 1000) - Math.round(b.y * 1000) || a.x - b.x);
  // Full rosters occupy the upper arc, with compact avatar previews below.
  const rank = (node: ChartNode) => node.kind === 'agent' ? 1 : node.members.length > 3 ? 2 : 0;
  const cards = [...nodes].sort((a, b) => rank(a) - rank(b)).map((node, index) => ({
    node, ...positions[index],
    width: node.kind === 'team' ? teamWidth : Math.min(282, teamWidth),
    height: node.kind === 'agent' ? 74 : node.members.length > 3 ? 128 : 102 + 44 * node.members.length,
  }));
  const boxes = [{ x: 0, y: 0, ...hubSize }, ...cards];
  let rx = Math.min(nodes.length < 3 ? 340 : 456, Math.max(140, (availableWidth - teamWidth - 40) / 2));
  let ry = Math.min(230, Math.max(140, availableHeight * 0.27));
  // Each pair needs clearance along at least one axis. First separate peers
  // on the same latitude, then increase the vertical radius only as needed.
  for (const [i, a] of boxes.entries()) {
    for (let j = i + 1; j < boxes.length; j++) {
      const b = boxes[j];
      if (Math.abs(a.y - b.y) < 0.001) rx = Math.max(rx, ((a.width + b.width) / 2 + gap) / Math.abs(a.x - b.x));
    }
  }
  for (const [i, a] of boxes.entries()) {
    for (let j = i + 1; j < boxes.length; j++) {
      const b = boxes[j];
      if (Math.abs(a.x - b.x) * rx + 0.01 < (a.width + b.width) / 2 + gap) {
        ry = Math.max(ry, ((a.height + b.height) / 2 + gap) / Math.abs(a.y - b.y));
      }
    }
  }
  const width = Math.ceil(Math.max(availableWidth, ...boxes.map(b => 2 * Math.abs(b.x * rx) + b.width + 40)));
  const height = Math.ceil(Math.max(availableHeight, ...boxes.map(b => 2 * Math.abs(b.y * ry) + b.height + 40)));
  const hub = { x: width / 2, y: height / 2, ...hubSize };
  return { width, height, hub, cards: cards.map(card => {
    const box = { ...card, x: hub.x + card.x * rx, y: hub.y + card.y * ry };
    // Curve out of the upper/lower hub edge for the diagonals. If the
    // destination sits beside the hub, meet its inward-facing side.
    const from = port(hub, box, Math.abs(card.x) > 1.5 * Math.abs(card.y));
    const to = port(box, hub, Math.abs(box.x - hub.x) > (box.width + hub.width) / 2);

    const bend = Math.min(100, Math.hypot(to.x - from.x, to.y - from.y) * 0.47);
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const startBend = Math.min(bend, Math.max(0, dx * from.nx + dy * from.ny) * 0.8);
    const endBend = Math.min(bend, Math.max(0, -dx * to.nx - dy * to.ny) * 0.8);
    const path = `M ${from.x} ${from.y} C ${from.x + from.nx * startBend} ${from.y + from.ny * startBend}, ${to.x + to.nx * endBend} ${to.y + to.ny * endBend}, ${to.x} ${to.y}`;
    return { ...box, from, to, path };
  }) };
}
