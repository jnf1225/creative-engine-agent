// MIGRATION REPLACEMENT for @lovable.dev/email-js `sendLovableEmail`.
//
// Drop-in adapter with a compatible signature so the email-queue processor
// (src/routes/lovable/email/queue/process.ts) changes by only its import + call
// name. Sends via Resend. Swap the fetch body for Postmark if you prefer — the
// shape is identical (from/to/subject/html/text + a couple of headers).
//
// Preserves the behaviors process.ts relies on:
//   - throws on non-2xx (so the existing retry / DLQ logic fires)
//   - surfaces a `.status` on the thrown error (so isRateLimited() sees 429)
//   - sets List-Unsubscribe + an idempotency header

type EmailPayload = {
  run_id?: string;
  to: string;
  from: string;
  sender_domain?: string;
  subject: string;
  html: string;
  text?: string;
  purpose?: string;
  label?: string;
  idempotency_key?: string;
  unsubscribe_token?: string;
  message_id?: string;
};

class EmailAPIError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "EmailAPIError";
    this.status = status;
  }
}

const RESEND_URL = "https://api.resend.com/emails";

export async function sendTransactionalEmail(
  payload: EmailPayload,
  opts: { apiKey?: string } = {},
): Promise<void> {
  const apiKey = opts.apiKey || process.env.RESEND_API_KEY;
  if (!apiKey) throw new Error("RESEND_API_KEY not configured");

  const headers: Record<string, string> = {};
  if (payload.message_id) headers["X-Entity-Ref-ID"] = payload.message_id;
  if (payload.idempotency_key) headers["Idempotency-Key"] = payload.idempotency_key;
  if (payload.unsubscribe_token && payload.sender_domain) {
    headers["List-Unsubscribe"] =
      `<https://${payload.sender_domain}/unsubscribe?token=${payload.unsubscribe_token}>`;
  }

  const res = await fetch(RESEND_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
      ...(payload.idempotency_key ? { "Idempotency-Key": payload.idempotency_key } : {}),
    },
    body: JSON.stringify({
      from: payload.from,
      to: payload.to,
      subject: payload.subject,
      html: payload.html,
      text: payload.text,
      headers,
    }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new EmailAPIError(
      `Email send failed (${res.status}): ${body.slice(0, 300)}`,
      res.status,
    );
  }
}

// Back-compat alias so process.ts can `import { sendLovableEmail } from "@/lib/email/send-email"`
// with a one-word import-path change and no call-site edits.
export const sendLovableEmail = sendTransactionalEmail;
