// A step-up login (LoA 2, OTP) runs in a popup. Its OAuth state carries this prefix so the
// callback knows to report back to the chat tab and close the popup instead of navigating.
export const STEP_UP_STATE_PREFIX = 'stepup.';

// Same-origin channel the callback page uses to tell the chat tab how the popup ended.
export const STEP_UP_CHANNEL = 'verify-step-up';

export type StepUpResult = 'complete' | 'failed';
