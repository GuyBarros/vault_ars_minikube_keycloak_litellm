import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Sidebar } from '@/components/sidebar';
import { AllowPill } from '@/components/badge';
import { ChevronLeftIcon, NamespaceIcon, ServiceIcon } from '@/components/icons';
import { api, ApiError } from '@/lib/api';
import { EditAllowForm } from './edit-form';

export const dynamic = 'force-dynamic';

type Params = { srcNs: string; srcSvc: string; dstNs: string; dstSvc: string };

export default async function RulePairPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { srcNs, srcSvc, dstNs, dstSvc } = await params;

  // Catalog load is required; if this errors we can't show anything useful.
  let version: number;
  let allow: string[];
  try {
    const data = await api.getRules();
    const bucket = data.catalog.rules?.[`${srcNs}/${srcSvc}`];
    const pair = bucket?.[`${dstNs}/${dstSvc}`];
    if (!pair) {
      notFound();
    }
    allow = pair.allow ?? [];
    version = data.version;
  } catch (err) {
    return (
      <PageShell srcNs={srcNs} srcSvc={srcSvc} dstNs={dstNs} dstSvc={dstSvc}>
        <ErrorBanner
          title="Could not load rule"
          message={
            err instanceof ApiError
              ? `${err.status}: ${err.message}`
              : err instanceof Error
                ? err.message
                : 'Unknown error contacting consul-mcp-authz'
          }
        />
      </PageShell>
    );
  }

  // Tool discovery is best-effort: surfaced when available so operators get
  // checkboxes rather than free-text, but a 503/502/404 from the API just
  // means we render the current allow list as-is with a manual add-tool box.
  let discoveredTools: string[] | null = null;
  let discoveryError: string | null = null;
  try {
    const tools = await api.getMcpServerTools(dstNs, dstSvc);
    discoveredTools = tools.tools.map((t) => t.name);
  } catch (err) {
    discoveryError =
      err instanceof ApiError
        ? `${err.status}: ${err.message}`
        : err instanceof Error
          ? err.message
          : 'Unknown discovery error';
  }

  return (
    <PageShell srcNs={srcNs} srcSvc={srcSvc} dstNs={dstNs} dstSvc={dstSvc}>
      <header className="mb-6">
        <Link
          href="/rules"
          className="inline-flex items-center gap-1 text-sm text-content-muted hover:text-content-ink"
        >
          <ChevronLeftIcon size={14} />
          Rules
        </Link>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-content-ink">
          {srcSvc} → {dstSvc}
        </h1>
        <p className="mt-1 text-sm text-content-muted">
          Edit the tool allow-list for this source → destination pair. Catalog
          version {version}.
        </p>
      </header>

      <div className="mb-6 grid grid-cols-[1fr_auto_1fr] gap-4 rounded-lg border border-content-border bg-white p-5">
        <ServiceBlock label="Source" namespace={srcNs} name={srcSvc} />
        <div className="flex items-center">
          <AllowPill />
        </div>
        <ServiceBlock label="Destination" namespace={dstNs} name={dstSvc} />
      </div>

      <EditAllowForm
        srcNs={srcNs}
        srcSvc={srcSvc}
        dstNs={dstNs}
        dstSvc={dstSvc}
        initialAllow={allow}
        initialVersion={version}
        discoveredTools={discoveredTools}
        discoveryError={discoveryError}
      />
    </PageShell>
  );
}

function PageShell({
  srcNs,
  srcSvc,
  dstNs,
  dstSvc,
  children,
}: {
  srcNs: string;
  srcSvc: string;
  dstNs: string;
  dstSvc: string;
  children: React.ReactNode;
}) {
  // Sidebar's active-nav still highlights /rules — the detail view lives
  // under that section.
  void srcNs;
  void srcSvc;
  void dstNs;
  void dstSvc;
  return (
    <div className="flex min-h-screen">
      <Sidebar activeHref="/rules" />
      <main className="min-w-0 flex-1 bg-content-bg">
        <div className="mx-auto max-w-[1100px] px-10 py-10">{children}</div>
      </main>
    </div>
  );
}

function ServiceBlock({
  label,
  namespace,
  name,
}: {
  label: string;
  namespace: string;
  name: string;
}) {
  return (
    <div>
      <div className="text-[11px] font-medium uppercase tracking-wide text-content-muted">
        {label}
      </div>
      <div className="mt-1 text-base font-semibold text-content-ink">{name}</div>
      <div className="mt-1 flex items-center gap-3 text-xs text-content-muted">
        <span className="inline-flex items-center gap-1">
          <ServiceIcon size={12} />
          default
        </span>
        <span className="inline-flex items-center gap-1">
          <NamespaceIcon size={12} />
          {namespace}
        </span>
      </div>
    </div>
  );
}

function ErrorBanner({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <div className="font-semibold">{title}</div>
      <div className="mt-1 font-mono text-xs">{message}</div>
    </div>
  );
}
