// Simplified Consul "C" mark in the brand pink/coral. Not the official
// asset — a hand-drawn approximation that reads well at 28px in the
// sidebar, which is all we use it for. Replace with the licensed mark
// later if needed.

export function ConsulLogo({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role="img"
      aria-label="Consul"
    >
      <circle cx="16" cy="16" r="15" fill="#CA2171" />
      <path
        d="M21 11.3a7 7 0 1 0 0 9.4"
        fill="none"
        stroke="#FFFFFF"
        strokeWidth="3.4"
        strokeLinecap="round"
      />
      <circle cx="22.8" cy="16" r="1.6" fill="#FFFFFF" />
    </svg>
  );
}
