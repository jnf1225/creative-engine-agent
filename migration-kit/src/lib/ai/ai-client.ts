// MIGRATION HELPER replacing the hardcoded Lovable AI Gateway.
//
// ai-analyst.server.ts currently hardcodes:
//     const GATEWAY_URL = "https://ai.gateway.lovable.dev/v1/chat/completions";
//     const MODEL = "google/gemini-2.5-flash";
//     ... Authorization: Bearer process.env.LOVABLE_API_KEY
//
// That endpoint is a Lovable platform service (billed via Lovable credits) and
// 401s off-platform. This helper points at any OpenAI-compatible chat-completions
// endpoint via env vars. Gemini exposes an OpenAI-compatible surface by default
// (see AI_GATEWAY_URL in env/web.env.example); OpenAI/Anthropic-compatible
// gateways work unchanged.
//
// See patches/EDITS.md → Edit 8 for the exact change to ai-analyst.server.ts.

export const AI_GATEWAY_URL =
  process.env.AI_GATEWAY_URL ||
  "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions";

export const AI_MODEL = process.env.AI_MODEL || "gemini-2.5-flash";

export function aiApiKey(): string {
  const key = process.env.AI_API_KEY || process.env.LOVABLE_API_KEY;
  if (!key) throw new Error("AI_API_KEY not configured");
  return key;
}

// Thin wrapper matching how ai-analyst.server.ts already calls the gateway:
// an OpenAI-compatible POST returning { choices: [{ message: { content } }] }.
export async function aiChatCompletion(body: {
  messages: Array<{ role: string; content: string }>;
  response_format?: unknown;
  temperature?: number;
}): Promise<Response> {
  return fetch(AI_GATEWAY_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${aiApiKey()}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ model: AI_MODEL, ...body }),
  });
}
