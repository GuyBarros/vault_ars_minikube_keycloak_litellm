// Mirrors models/schemas.py on the API side. Kept manually in sync — small
// surface and the API never returns extra fields, so a generator would be
// more ceremony than value at this size.

export type PairAllow = {
  allow: string[];
};

export type RulesMap = Record<string, Record<string, PairAllow>>;

export type Catalog = {
  rules: RulesMap;
};

export type CatalogRead = {
  catalog: Catalog;
  version: number;
  created_time: string | null;
};

export type ServiceRef = {
  namespace: string;
  name: string;
};

export type ToolInfo = {
  name: string;
  description: string | null;
};

export type McpServerTools = {
  namespace: string;
  name: string;
  tools: ToolInfo[];
  source: string;
};

export type CatalogWriteResponse = {
  version: number;
  created_time: string | null;
};

export type AgentList = {
  agents: ServiceRef[];
};

export type McpServerList = {
  mcp_servers: ServiceRef[];
};

export type VersionInfo = {
  version: number;
  created_time: string | null;
  deletion_time: string | null;
  destroyed: boolean;
};

export type HistoryResponse = {
  current_version: number;
  versions: VersionInfo[];
};

// UI-flattened row: one entry per (src-ns, src-svc, dst-ns, dst-svc).
export type RuleRow = {
  srcNamespace: string;
  srcName: string;
  dstNamespace: string;
  dstName: string;
  allow: string[];
};

export function flattenCatalog(catalog: Catalog): RuleRow[] {
  const rows: RuleRow[] = [];
  for (const [srcKey, dests] of Object.entries(catalog.rules ?? {})) {
    const [srcNamespace, srcName] = splitKey(srcKey);
    for (const [dstKey, pair] of Object.entries(dests ?? {})) {
      const [dstNamespace, dstName] = splitKey(dstKey);
      rows.push({
        srcNamespace,
        srcName,
        dstNamespace,
        dstName,
        allow: pair.allow ?? [],
      });
    }
  }
  // Sort by source-then-destination for deterministic display.
  rows.sort((a, b) =>
    `${a.srcNamespace}/${a.srcName}/${a.dstNamespace}/${a.dstName}`.localeCompare(
      `${b.srcNamespace}/${b.srcName}/${b.dstNamespace}/${b.dstName}`,
    ),
  );
  return rows;
}

function splitKey(key: string): [string, string] {
  const slash = key.indexOf('/');
  if (slash < 0) {
    return ['default', key];
  }
  return [key.slice(0, slash), key.slice(slash + 1)];
}
