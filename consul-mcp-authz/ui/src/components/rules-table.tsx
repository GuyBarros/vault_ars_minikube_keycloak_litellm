import Link from 'next/link';
import type { RuleRow } from '@/lib/types';
import { AllowPill, ToolCountPill } from './badge';
import { MoreIcon, NamespaceIcon, ServiceIcon } from './icons';

export function RulesTable({ rows }: { rows: RuleRow[] }) {
  if (rows.length === 0) {
    return <EmptyState />;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-content-border bg-white">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="bg-content-subtle text-left text-xs font-medium uppercase tracking-wide text-content-muted">
            <th className="px-4 py-3 font-medium">Source</th>
            <th className="px-4 py-3 font-medium">Action</th>
            <th className="px-4 py-3 font-medium">Destination</th>
            <th className="px-4 py-3 font-medium">Permissions</th>
            <th className="w-12 px-2 py-3"></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const href = `/rules/${encodeURIComponent(row.srcNamespace)}/${encodeURIComponent(
              row.srcName,
            )}/${encodeURIComponent(row.dstNamespace)}/${encodeURIComponent(row.dstName)}`;
            return (
              <tr
                key={`${row.srcNamespace}/${row.srcName}/${row.dstNamespace}/${row.dstName}`}
                className={`group cursor-pointer transition-colors hover:bg-content-subtle ${
                  i === rows.length - 1 ? '' : 'border-b border-content-divider'
                }`}
              >
                <td className="px-4 py-3 align-top">
                  <Link href={href} className="block">
                    <ServiceCell namespace={row.srcNamespace} name={row.srcName} />
                  </Link>
                </td>
                <td className="px-4 py-3 align-top">
                  <Link href={href} className="block">
                    <AllowPill />
                  </Link>
                </td>
                <td className="px-4 py-3 align-top">
                  <Link href={href} className="block">
                    <ServiceCell namespace={row.dstNamespace} name={row.dstName} />
                  </Link>
                </td>
                <td className="px-4 py-3 align-top">
                  <Link href={href} className="block">
                    <ToolCountPill count={row.allow.length} />
                  </Link>
                </td>
                <td className="px-2 py-3 align-top text-right">
                  <button
                    type="button"
                    className="rounded p-1 text-content-muted hover:bg-content-divider"
                    aria-label="Row actions"
                  >
                    <MoreIcon size={16} />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ServiceCell({ namespace, name }: { namespace: string; name: string }) {
  return (
    <div className="flex flex-col">
      <span className="font-medium text-content-ink group-hover:text-brand">{name}</span>
      <span className="mt-0.5 flex items-center gap-3 text-xs text-content-muted">
        <span className="inline-flex items-center gap-1">
          <ServiceIcon size={12} />
          default
        </span>
        <span className="inline-flex items-center gap-1">
          <NamespaceIcon size={12} />
          {namespace}
        </span>
      </span>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-content-border bg-white px-6 py-16 text-center">
      <h2 className="text-base font-semibold text-content-ink">No rules yet</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-content-muted">
        No source → destination pairs are currently allow-listed in the catalog.
        Use the API or the Create button (coming soon) to add the first rule.
      </p>
    </div>
  );
}
