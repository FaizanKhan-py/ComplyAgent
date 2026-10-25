import type { CustomerMessage } from '../api/types'

/**
 * Sends a drafted email to the customer.
 *
 * Email delivery is not connected yet, so this only records the send in the page: nothing
 * leaves the browser. When the backend can send email, replace the body with the API call;
 * the page already shows the confirmation, the sending state and the result.
 */
export async function sendEmail(
  message: CustomerMessage,
  to: string,
): Promise<{ sentAt: Date; to: string; delivered: boolean }> {
  void message
  return { sentAt: new Date(), to, delivered: false }
}
