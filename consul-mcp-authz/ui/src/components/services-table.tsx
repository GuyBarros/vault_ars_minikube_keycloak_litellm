import Link from 'next/link';
import type { ServiceRef } from '@/lib/types';
import { NamespaceIcon, ServiceIcon } from './icons';

type Props = {
  services: ServiceRef[];
  emptyTitle: string;
  emptyBody: string;
  // Optional row link: when provided, the service name is rendered as a link
  // to the returned path. Used to wire the MCP Servers list to its detail page.
  linkFor?: (service: ServiceRef) => string;
};

export function ServicesTable({ services, emptyTitle, emptyBody, linkFor }: Props) {
  if (services.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-content-border bg-white px-6 py-16 text-center">
        <h2 className="text-base font-semibold text-content-ink">{emptyTitle}</h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-content-muted">{emptyBody}</p>
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-content-border bg-white">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="bg-content-subtle text-left text-xs font-medium uppercase tracking-wide text-content-muted">
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">Namespace</th>
          </tr>
        </thead>
        <tbody>
          {services.map((s, i) => {
            const href = linkFor?.(s);
            return (
              <tr
                key={`${s.namespace}/${s.name}`}
                className={
                  i === services.length - 1 ? '' : 'border-b border-content-divider'
                }
              >
                <td className="px-4 py-3 align-top">
                  {href ? (
                    <Link
                      href={href}
                      className="inline-flex items-center gap-2 font-medium text-brand hover:underline"
                    >
                      <ServiceIcon size={12} className="text-content-muted" />
                      {s.name}
                    </Link>
                  ) : (
                    <span className="inline-flex items-center gap-2 font-medium text-content-ink">
                      <ServiceIcon size={12} className="text-content-muted" />
                      {s.name}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 align-top text-content-muted">
                  <span className="inline-flex items-center gap-1">
                    <NamespaceIcon size={12} />
                    {s.namespace}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
