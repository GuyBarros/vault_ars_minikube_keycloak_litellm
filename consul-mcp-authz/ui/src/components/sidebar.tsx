import Link from 'next/link';
import { ConsulLogo } from './consul-logo';
import { ChevronDownIcon, NamespaceIcon, ServiceIcon, SnowflakeIcon } from './icons';

type NavItem = {
  label: string;
  href: string;
  active?: boolean;
  disabled?: boolean;
};

type NavSection = {
  label?: string;
  items: NavItem[];
};

export function Sidebar({ activeHref }: { activeHref: string }) {
  const sections: NavSection[] = [
    {
      items: [
        { label: 'Overview', href: '#', disabled: true },
        { label: 'Agents', href: '/agents' },
        { label: 'MCP Servers', href: '/mcp-servers' },
        { label: 'Rules', href: '/rules' },
        { label: 'History', href: '/history' },
      ],
    },
  ];

  return (
    <aside className="flex h-screen w-[240px] shrink-0 flex-col bg-nav-bg text-nav-text">
      <div className="px-4 pt-4">
        <div className="flex items-center justify-between">
          <ConsulLogo />
          <div className="flex items-center gap-2">
            <SelectorChip glyph="?" />
            <SelectorChip glyph="👤" />
          </div>
        </div>

        <div className="mt-4 flex items-center gap-2 text-sm">
          <SnowflakeIcon size={14} className="text-nav-muted" />
          <span className="font-medium">dc1</span>
        </div>
      </div>

      <div className="mt-5 space-y-4 px-4">
        <ScopeSelector label="Admin partition" value="default" icon="service" />
        <ScopeSelector label="Namespace" value="default" icon="namespace" />
      </div>

      <nav className="mt-6 flex-1 overflow-y-auto px-2 pb-6">
        {sections.map((section, i) => (
          <div key={i} className="mb-4">
            {section.label ? (
              <div className="px-3 pb-2 pt-3 text-[11px] font-medium uppercase tracking-wide text-nav-muted">
                {section.label}
              </div>
            ) : null}
            <ul className="space-y-0.5">
              {section.items.map((item) => {
                const isActive = !item.disabled && item.href === activeHref;
                const base =
                  'flex h-9 items-center rounded-md px-3 text-sm transition-colors';
                if (item.disabled) {
                  return (
                    <li key={item.label}>
                      <span
                        className={`${base} cursor-not-allowed text-nav-muted/70`}
                        aria-disabled
                      >
                        {item.label}
                      </span>
                    </li>
                  );
                }
                return (
                  <li key={item.label}>
                    <Link
                      href={item.href}
                      className={`${base} ${
                        isActive
                          ? 'bg-nav-selected text-white'
                          : 'text-nav-text hover:bg-nav-surface'
                      }`}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-nav-border px-4 py-3 text-xs text-nav-muted">
        consul-mcp-authz · pilot
      </div>
    </aside>
  );
}

function SelectorChip({ glyph }: { glyph: string }) {
  return (
    <button
      type="button"
      className="flex h-7 w-9 items-center justify-center gap-0.5 rounded-md border border-nav-border bg-nav-surface text-xs text-nav-text hover:bg-nav-selected"
    >
      <span aria-hidden>{glyph}</span>
      <ChevronDownIcon size={10} />
    </button>
  );
}

function ScopeSelector({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon: 'service' | 'namespace';
}) {
  return (
    <div>
      <div className="pb-1.5 text-[11px] font-medium uppercase tracking-wide text-nav-muted">
        {label}
      </div>
      <button
        type="button"
        className="flex w-full items-center justify-between rounded-md border border-nav-border bg-nav-surface px-2.5 py-1.5 text-left text-sm text-nav-text hover:bg-nav-selected"
      >
        <span className="flex items-center gap-2">
          {icon === 'service' ? (
            <ServiceIcon size={14} className="text-nav-muted" />
          ) : (
            <NamespaceIcon size={14} className="text-nav-muted" />
          )}
          {value}
        </span>
        <ChevronDownIcon size={14} className="text-nav-muted" />
      </button>
    </div>
  );
}
