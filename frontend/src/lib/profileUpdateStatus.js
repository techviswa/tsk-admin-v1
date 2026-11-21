const UNFINISHED = new Set(['pending', 'running', 'retrying', 'failed']);

export const profileUpdateBlocked = user => UNFINISHED.has(user?.pos_update_status);

export function profileUpdateMessage(user) {
  const status = user?.pos_update_status;
  if (status === 'rejected') return `POS rejected the previous update. Correct the details and save again.${user.pos_update_error ? ` ${user.pos_update_error}` : ''}`;
  if (!UNFINISHED.has(status)) return '';
  const retry = user.pos_update_retry_at ? new Date(user.pos_update_retry_at) : null;
  const retryText = retry && !Number.isNaN(retry.getTime()) ? ` Next attempt: ${retry.toLocaleString()}.` : '';
  const reason = user.pos_update_error ? ` ${user.pos_update_error}` : '';
  if (status === 'failed') return `The previous update failed. Retry that update before making another change.${reason}`;
  if (status === 'retrying') return `The credential update is waiting to retry; the saved user profile has not been updated.${retryText}${reason}`;
  return 'The previous update is still syncing with POS. Wait for completion before making another change.';
}
