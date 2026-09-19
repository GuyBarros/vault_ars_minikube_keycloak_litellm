'use client';

import { useEffect, useRef, useState } from 'react';

type NodeDef = {
  key: string;
  x: number;
  y: number;
  label: string;
  role: string;
  tool: string;
  color: string;
};

const AGENT_Y = 48;
const HUB_Y = 188;
const TOOL_Y = 338;
const ARC_DIP = 58;

const NODES: NodeDef[] = [
  { key: 'agent', x: 470, y: AGENT_Y, label: 'AI Agent', role: 'orquestra', tool: '/v1/agent/query', color: '#009d9a' },
  { key: 'opa', x: 655, y: AGENT_Y, label: 'OPA', role: 'PDP', tool: 'mcp.pep /decision', color: '#a56eff' },
  { key: 'user', x: 55, y: HUB_Y, label: 'User', role: 'canal', tool: 'browser', color: '#0f62fe' },
  { key: 'webapp', x: 215, y: HUB_Y, label: 'Web App', role: 'BFF', tool: 'login · /api/agent/query', color: '#33b1ff' },
  { key: 'pep', x: 470, y: HUB_Y, label: 'LiteLLM', role: 'PEP + AI Gateway', tool: 'pdp_auth · pdp_mcp', color: '#8a3ffc' },
  { key: 'obo', x: 760, y: HUB_Y, label: 'token-exchange', role: 'broker OBO', tool: 'RFC 8693 · /obo-token', color: '#ee5396' },
  { key: 'verify', x: 1040, y: HUB_Y, label: 'Keycloak', role: 'IdP', tool: 'login · OBO · CIBA', color: '#da1e28' },
  { key: 'ciba', x: 1320, y: HUB_Y, label: 'ciba-channel', role: 'HITL LoA2', tool: 'Approve/Deny :8082', color: '#fa4d56' },
  { key: 'mcp', x: 760, y: TOOL_Y, label: 'user-mcp', role: 'runtime MCP', tool: 'SQL · jwt_identity_bound', color: '#198038' },
  { key: 'vault', x: 1040, y: TOOL_Y, label: 'Vault', role: 'segredos', tool: 'database/creds · Transform', color: '#ff832b' },
  { key: 'db', x: 1320, y: TOOL_Y, label: 'Database', role: 'dados', tool: 'Postgres', color: '#6929c4' },
];

const NODE_BY_KEY: Record<string, NodeDef> = Object.fromEntries(
  NODES.map((n) => [n.key, n] as const),
);

const R = 11;
const PEP_R = 17;
const OPA_R = 13;
const radiusOf = (key: string) =>
  key === 'pep' ? PEP_R : key === 'opa' ? OPA_R : R;

const BOUNDARY = { x: 385, y: 10, width: 330, height: 248 };

function lineSegment(fromKey: string, toKey: string, headRoom = 8) {
  const a = NODE_BY_KEY[fromKey]!;
  const b = NODE_BY_KEY[toKey]!;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const ra = radiusOf(fromKey);
  const rb = radiusOf(toKey);
  return `M ${a.x + ux * (ra + 2)} ${a.y + uy * (ra + 2)} L ${b.x - ux * (rb + headRoom)} ${b.y - uy * (rb + headRoom)}`;
}

function verticalOffset(fromKey: string, toKey: string, xOffset: number, headRoom = 8) {
  const a = NODE_BY_KEY[fromKey]!;
  const b = NODE_BY_KEY[toKey]!;
  const ra = radiusOf(fromKey);
  const rb = radiusOf(toKey);
  const down = b.y > a.y;
  const sy = a.y + (down ? ra + 2 : -(ra + 2));
  const ey = b.y + (down ? -(rb + headRoom) : rb + headRoom);
  return `M ${a.x + xOffset} ${sy} L ${b.x + xOffset} ${ey}`;
}

function dist(fromKey: string, toKey: string) {
  const a = NODE_BY_KEY[fromKey]!;
  const b = NODE_BY_KEY[toKey]!;
  return Math.hypot(b.x - a.x, b.y - a.y);
}

function quadLen(fromKey: string, toKey: string, cpx: number, cpy: number) {
  const a = NODE_BY_KEY[fromKey]!;
  const b = NODE_BY_KEY[toKey]!;
  const chord = Math.hypot(b.x - a.x, b.y - a.y);
  const poly =
    Math.hypot(cpx - a.x, cpy - a.y) + Math.hypot(b.x - cpx, b.y - cpy);
  return (chord + poly) / 2;
}

type Edge = { d: string; arrowEnd?: boolean };

const STATIC_EDGES: Edge[] = [
  { d: lineSegment('user', 'webapp'), arrowEnd: true },
  { d: lineSegment('webapp', 'pep'), arrowEnd: true },
  { d: lineSegment('pep', 'obo'), arrowEnd: true },
  { d: lineSegment('obo', 'verify'), arrowEnd: true },
  { d: lineSegment('verify', 'ciba'), arrowEnd: true },

  { d: verticalOffset('pep', 'agent', -7), arrowEnd: true },
  { d: verticalOffset('agent', 'pep', +7), arrowEnd: true },
  { d: lineSegment('pep', 'opa'), arrowEnd: true },

  { d: lineSegment('pep', 'mcp') },
  { d: lineSegment('mcp', 'vault'), arrowEnd: true },
  {
    d: `M ${NODE_BY_KEY.mcp!.x + R + 2} ${TOOL_Y + 2} Q ${(NODE_BY_KEY.mcp!.x + NODE_BY_KEY.db!.x) / 2} ${TOOL_Y + ARC_DIP} ${NODE_BY_KEY.db!.x - R - 10} ${TOOL_Y + 4} L ${NODE_BY_KEY.db!.x - R - 6} ${TOOL_Y}`,
    arrowEnd: true,
  },
];

const N = NODE_BY_KEY;
const MID_MCP_DB_X = (N.mcp!.x + N.db!.x) / 2;

// LoA1 list_users: Consul front door → PEP admit → orquestra → OBO → PDP → runtime.
const MOTION_PATH = [
  `M ${N.user!.x} ${HUB_Y}`,
  `L ${N.webapp!.x} ${HUB_Y}`,
  `L ${N.pep!.x} ${HUB_Y}`,
  `L ${N.agent!.x} ${AGENT_Y}`,
  `L ${N.pep!.x} ${HUB_Y}`,
  `L ${N.obo!.x} ${HUB_Y}`,
  `L ${N.verify!.x} ${HUB_Y}`,
  `L ${N.obo!.x} ${HUB_Y}`,
  `L ${N.pep!.x} ${HUB_Y}`,
  `L ${N.opa!.x} ${AGENT_Y}`,
  `L ${N.pep!.x} ${HUB_Y}`,
  `L ${N.mcp!.x} ${TOOL_Y}`,
  `L ${N.vault!.x} ${TOOL_Y}`,
  `L ${N.mcp!.x} ${TOOL_Y}`,
  `Q ${MID_MCP_DB_X} ${TOOL_Y + ARC_DIP} ${N.db!.x} ${TOOL_Y}`,
  `Q ${MID_MCP_DB_X} ${TOOL_Y + ARC_DIP} ${N.mcp!.x} ${TOOL_Y}`,
  `L ${N.pep!.x} ${HUB_Y}`,
  `L ${N.agent!.x} ${AGENT_Y}`,
  `L ${N.pep!.x} ${HUB_Y}`,
  `L ${N.webapp!.x} ${HUB_Y}`,
  `L ${N.user!.x} ${HUB_Y}`,
].join(' ');

const TRAIL_OFFSETS = [0, 0.18, 0.34, 0.5, 0.66];
const MOTION_DUR = 38;
const PEP_HOP_SLOWDOWN = 3;

const SEGMENT_LABELS = [
  'subject_token + prompt',
  'SPIFFE default/web + JWT',
  '/v1/agent/query',
  'tool + required_scopes',
  'OBO RFC 8693',
  'subject + actor + scopes',
  'OBO JWT aud=user-mcp',
  'tools/call',
  'POST mcp.pep/decision',
  'enforce=inject_obo_jwt',
  'X-Vault-Token',
  'database/creds',
  'JIT Postgres',
  'SQL result',
  'tool result',
  'result',
  'chat response',
  'sanitized result',
  'sanitized result',
  'sanitized result',
];

const SEGMENT_NODES: ReadonlyArray<readonly [string, string]> = [
  ['user', 'webapp'],
  ['webapp', 'pep'],
  ['pep', 'agent'],
  ['agent', 'pep'],
  ['pep', 'obo'],
  ['obo', 'verify'],
  ['verify', 'obo'],
  ['obo', 'pep'],
  ['pep', 'opa'],
  ['opa', 'pep'],
  ['pep', 'mcp'],
  ['mcp', 'vault'],
  ['vault', 'mcp'],
  ['mcp', 'db'],
  ['db', 'mcp'],
  ['mcp', 'pep'],
  ['pep', 'agent'],
  ['agent', 'pep'],
  ['pep', 'webapp'],
  ['webapp', 'user'],
];

const SEGMENT_LENGTHS = SEGMENT_NODES.map(([from, to]) => {
  if (from === 'mcp' && to === 'db') return quadLen('mcp', 'db', MID_MCP_DB_X, TOOL_Y + ARC_DIP);
  if (from === 'db' && to === 'mcp') return quadLen('db', 'mcp', MID_MCP_DB_X, TOOL_Y + ARC_DIP);
  return dist(from, to);
});

const isPepHop = (i: number) => {
  const [from, to] = SEGMENT_NODES[i]!;
  return (
    (from === 'pep' && (to === 'agent' || to === 'opa')) ||
    ((from === 'agent' || from === 'opa') && to === 'pep')
  );
};

const SEGMENT_WEIGHTS = SEGMENT_LENGTHS.map((len, i) =>
  isPepHop(i) ? len * PEP_HOP_SLOWDOWN : len,
);

const TOTAL_LENGTH = SEGMENT_LENGTHS.reduce((a, b) => a + b, 0);
const TOTAL_WEIGHT = SEGMENT_WEIGHTS.reduce((a, b) => a + b, 0);

const cumulative = (arr: readonly number[]) =>
  arr.reduce<number[]>(
    (acc, v) => {
      const last = acc[acc.length - 1] ?? 0;
      acc.push(last + v);
      return acc;
    },
    [0],
  );

const PATH_POINTS = cumulative(SEGMENT_LENGTHS).map((l) => l / TOTAL_LENGTH);
const TIME_POINTS = cumulative(SEGMENT_WEIGHTS).map((w) => w / TOTAL_WEIGHT);

const KEY_POINTS_STR = PATH_POINTS.map((p) => p.toFixed(4)).join(';');
const KEY_TIMES_STR = TIME_POINTS.map((t) => t.toFixed(4)).join(';');

const segmentFraction = (idx: number) =>
  (TIME_POINTS[idx] ?? 0).toFixed(4);

export function FlowStrip() {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReducedMotion(mq.matches);
    const onChange = () => setReducedMotion(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    if (reducedMotion || collapsed) {
      svg.pauseAnimations?.();
    } else {
      svg.unpauseAnimations?.();
    }
  }, [reducedMotion, collapsed]);

  const handleEnter = () => {
    if (!collapsed) svgRef.current?.pauseAnimations?.();
  };
  const handleLeave = () => {
    if (!reducedMotion && !collapsed) svgRef.current?.unpauseAnimations?.();
  };

  return (
    <section
      className={`flow-strip${collapsed ? ' flow-strip--collapsed' : ''}`}
      aria-label="Fluxo do lab: Consul, LiteLLM PEP, OPA PDP, user-mcp"
    >
      <div className="flow-strip__header">
        <span className="flow-strip__title">
          Lab flow · PEP LiteLLM · PDP OPA
        </span>
        <button
          type="button"
          className="flow-strip__toggle"
          aria-expanded={!collapsed}
          aria-controls="flow-strip-body"
          onClick={() => setCollapsed((c) => !c)}
        >
          <span className="flow-strip__toggle-label">
            {collapsed ? 'Show' : 'Hide'}
          </span>
          <svg
            className="flow-strip__chevron"
            width="14"
            height="14"
            viewBox="0 0 16 16"
            aria-hidden="true"
          >
            <path
              d="M3 6l5 5 5-5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>

      <div
        id="flow-strip-body"
        className="flow-strip__body"
        hidden={collapsed}
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
      >
        <svg
          ref={svgRef}
          className="flow-strip__svg"
          viewBox="0 0 1540 430"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-hidden="true"
        >
          <defs>
            <marker
              id="fs-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fs-arrow-head" />
            </marker>
            <path id="fs-motion-path" d={MOTION_PATH} fill="none" />

            <radialGradient id="fs-pep-halo" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#8a3ffc" stopOpacity="0.28" />
              <stop offset="60%" stopColor="#8a3ffc" stopOpacity="0.08" />
              <stop offset="100%" stopColor="#8a3ffc" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="fs-opa-halo" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#a56eff" stopOpacity="0.22" />
              <stop offset="70%" stopColor="#a56eff" stopOpacity="0.06" />
              <stop offset="100%" stopColor="#a56eff" stopOpacity="0" />
            </radialGradient>
          </defs>

          <circle
            cx={N.pep!.x}
            cy={N.pep!.y}
            r={PEP_R + 22}
            fill="url(#fs-pep-halo)"
          />
          <circle
            cx={N.opa!.x}
            cy={N.opa!.y}
            r={OPA_R + 16}
            fill="url(#fs-opa-halo)"
          />

          <rect
            className="fs-boundary"
            x={BOUNDARY.x}
            y={BOUNDARY.y}
            width={BOUNDARY.width}
            height={BOUNDARY.height}
            rx={8}
            ry={8}
          />
          <text
            className="fs-boundary-title"
            x={BOUNDARY.x + 8}
            y={BOUNDARY.y + 14}
          >
            PEP / PDP
          </text>

          {STATIC_EDGES.map((e, i) => (
            <path
              key={i}
              className="fs-edge"
              d={e.d}
              markerEnd={e.arrowEnd ? 'url(#fs-arrow)' : undefined}
            />
          ))}

          <text
            x={(N.user!.x + N.webapp!.x) / 2}
            y={HUB_Y - 14}
            textAnchor="middle"
            className="fs-annotation"
          >
            Consul API GW :8080 · mTLS
          </text>
          <text
            x={(N.pep!.x + N.obo!.x) / 2}
            y={HUB_Y - 16}
            textAnchor="middle"
            className="fs-annotation"
          >
            tool / required_scopes
          </text>

          {NODES.map((n) => {
            const r = radiusOf(n.key);
            return (
              <g key={n.key} className="fs-node" data-key={n.key}>
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={r}
                  style={{ fill: n.color, stroke: n.color }}
                />
                <text
                  x={n.x}
                  y={n.y + r + 16}
                  textAnchor="middle"
                  className="fs-label"
                >
                  {n.label}
                </text>
                <text
                  x={n.x}
                  y={n.y + r + 30}
                  textAnchor="middle"
                  className="fs-role"
                >
                  {n.role}
                </text>
                <text
                  x={n.x}
                  y={n.y + r + 43}
                  textAnchor="middle"
                  className="fs-tool"
                >
                  {n.tool}
                </text>
              </g>
            );
          })}

          <g className="fs-particles">
            {TRAIL_OFFSETS.map((offset, i) => {
              const opacity = Math.max(0.18, 1 - i * 0.2);
              const r = Math.max(2.5, 5 - i * 0.6);
              return (
                <circle key={i} r={r} className="fs-particle" style={{ opacity }}>
                  <animateMotion
                    dur={`${MOTION_DUR}s`}
                    begin={`${offset}s`}
                    repeatCount="indefinite"
                    calcMode="linear"
                    keyPoints={KEY_POINTS_STR}
                    keyTimes={KEY_TIMES_STR}
                  >
                    <mpath href="#fs-motion-path" />
                  </animateMotion>
                </circle>
              );
            })}
          </g>

          <g className="fs-flow-labels">
            {SEGMENT_LABELS.map((label, i) => {
              const isFirst = i === 0;
              const isLast = i === SEGMENT_LABELS.length - 1;
              const f0 = segmentFraction(i);
              const f1 = segmentFraction(i + 1);
              const keyTimes = isFirst
                ? `0;${f1};1`
                : isLast
                  ? `0;${f0};1`
                  : `0;${f0};${f1};1`;
              const values = isFirst
                ? '1;0;0'
                : isLast
                  ? '0;1;1'
                  : '0;1;0;0';
              return (
                <g key={i}>
                  <text
                    y={-18}
                    textAnchor="middle"
                    className="fs-flow-label"
                    opacity={0}
                  >
                    {label}
                    <animate
                      attributeName="opacity"
                      dur={`${MOTION_DUR}s`}
                      repeatCount="indefinite"
                      calcMode="discrete"
                      keyTimes={keyTimes}
                      values={values}
                    />
                  </text>
                  <animateMotion
                    dur={`${MOTION_DUR}s`}
                    repeatCount="indefinite"
                    rotate="0"
                    calcMode="linear"
                    keyPoints={KEY_POINTS_STR}
                    keyTimes={KEY_TIMES_STR}
                  >
                    <mpath href="#fs-motion-path" />
                  </animateMotion>
                </g>
              );
            })}
          </g>
        </svg>
      </div>
    </section>
  );
}
