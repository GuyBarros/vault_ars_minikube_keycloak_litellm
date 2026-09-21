export interface LoaState {
  /** Level of assurance of the session token (Keycloak's acr): 1 = password, 2 = OTP step-up. */
  level: number;
  /** Epoch seconds when the level-2 step-up lapses; null when not elevated (or already lapsed). */
  elevatedUntil: number | null;
  /** Length of a step-up window, seconds. */
  ttlSeconds: number;
}

/** The session token's LoA and when a step-up (acr >= 2) stops counting, per the PEP's rule. */
export function loaState(claims: Record<string, unknown>, ttlSeconds: number, nowSeconds: number): LoaState {
  const level = Number(claims.acr) || 1;
  const earned = Number(claims.acr_time ?? claims.iat);
  if (level < 2 || !Number.isFinite(earned)) return { level: 1, elevatedUntil: null, ttlSeconds };
  const until = earned + ttlSeconds;
  return until > nowSeconds ? { level, elevatedUntil: until, ttlSeconds } : { level: 1, elevatedUntil: null, ttlSeconds };
}
