import { ArrowRightIcon } from './icons';

export function AllowPill() {
  return (
    <span className="pill-allow">
      <ArrowRightIcon size={11} className="text-allow-ink" />
      Allow
    </span>
  );
}

export function ToolCountPill({ count }: { count: number }) {
  // Consul shows "Permissions" with an info-i; we instead show how many
  // tools this pair's allow list contains, which is the destination's
  // analog under our authorization model.
  if (count === 0) {
    return (
      <span className="pill-status text-content-muted">no tools allowed</span>
    );
  }
  return (
    <span className="pill-status">
      {count} {count === 1 ? 'tool' : 'tools'} allowed
    </span>
  );
}
