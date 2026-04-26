/**
 * Cloudflare Pages Function для waitlist endpoint.
 * Path: POST /api/waitlist
 *
 * Storage:
 *   - Primary: KV namespace `WAITLIST` (bind в Cloudflare Pages settings)
 *   - Optional: forward email-уведомление через Resend (если задан RESEND_API_KEY)
 *
 * Все нечувствительные данные отправляются как JSON. PII (email) — только в KV
 * с automatic TTL = 365 days, пока не построен полноценный backend.
 *
 * Безопасность:
 *   - Email validation (RFC 5322 minimal)
 *   - Rate limit: 5 submissions / IP / hour (in-memory через KV)
 *   - CORS — same-origin only (Pages Function default)
 */

interface Env {
  WAITLIST: KVNamespace;
  RESEND_API_KEY?: string;
  NOTIFICATION_TO?: string;
}

interface WaitlistPayload {
  email: string;
  name: string | null;
  wants_upload: boolean;
  source: string;
  submitted_at: string;
}

interface StoredEntry extends WaitlistPayload {
  ip_hash: string;
  user_agent: string;
  country: string;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const RATE_LIMIT_WINDOW_S = 3600;
const RATE_LIMIT_MAX = 5;

function badRequest(message: string): Response {
  return new Response(JSON.stringify({ error: message }), {
    status: 400,
    headers: { "Content-Type": "application/json" },
  });
}

async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function checkRateLimit(
  kv: KVNamespace,
  ipHash: string,
): Promise<{ ok: boolean; remaining: number }> {
  const key = `rl:${ipHash}`;
  const raw = await kv.get(key);
  const count = raw ? Number.parseInt(raw, 10) : 0;
  if (count >= RATE_LIMIT_MAX) {
    return { ok: false, remaining: 0 };
  }
  await kv.put(key, String(count + 1), {
    expirationTtl: RATE_LIMIT_WINDOW_S,
  });
  return { ok: true, remaining: RATE_LIMIT_MAX - count - 1 };
}

async function sendNotification(env: Env, entry: StoredEntry): Promise<void> {
  if (!env.RESEND_API_KEY || !env.NOTIFICATION_TO) return;

  const html = `
    <h2>New AutoTreeGen waitlist signup</h2>
    <ul>
      <li><strong>Email:</strong> ${entry.email}</li>
      <li><strong>Name:</strong> ${entry.name ?? "—"}</li>
      <li><strong>Wants GEDCOM upload:</strong> ${entry.wants_upload ? "yes" : "no"}</li>
      <li><strong>Source:</strong> ${entry.source}</li>
      <li><strong>Country:</strong> ${entry.country}</li>
      <li><strong>Submitted:</strong> ${entry.submitted_at}</li>
    </ul>
  `;

  await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: "AutoTreeGen <waitlist@autotreegen.com>",
      to: [env.NOTIFICATION_TO],
      subject: `New waitlist signup: ${entry.email}`,
      html,
    }),
  }).catch(() => {
    /* swallow — notification is best-effort */
  });
}

export const onRequestPost: PagesFunction<Env> = async (context) => {
  const { request, env } = context;

  // Parse + validate payload
  let payload: WaitlistPayload;
  try {
    payload = (await request.json()) as WaitlistPayload;
  } catch {
    return badRequest("Invalid JSON payload.");
  }

  if (!payload.email || !EMAIL_RE.test(payload.email)) {
    return badRequest("A valid email is required.");
  }
  if (payload.email.length > 254) {
    return badRequest("Email is too long.");
  }
  if (payload.name && payload.name.length > 200) {
    return badRequest("Name is too long.");
  }

  // Rate limit by IP hash
  const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
  const ipHash = await sha256Hex(ip);
  const rl = await checkRateLimit(env.WAITLIST, ipHash);
  if (!rl.ok) {
    return new Response(JSON.stringify({ error: "Too many requests. Try again later." }), {
      status: 429,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Store
  const entry: StoredEntry = {
    ...payload,
    email: payload.email.toLowerCase(),
    ip_hash: ipHash,
    user_agent: request.headers.get("User-Agent") ?? "",
    // biome-ignore lint/suspicious/noExplicitAny: Cloudflare types — request.cf is loosely typed
    country: ((request as any).cf?.country as string | undefined) ?? "??",
  };

  const key = `waitlist:${entry.email}`;
  const existing = await env.WAITLIST.get(key);
  if (existing) {
    // Idempotent — already on the list
    return new Response(JSON.stringify({ ok: true, duplicate: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  await env.WAITLIST.put(key, JSON.stringify(entry), {
    // 1 year TTL — мы либо контактнём раньше, либо просим повторно подтвердить
    expirationTtl: 365 * 24 * 3600,
    metadata: {
      submitted_at: entry.submitted_at,
      wants_upload: entry.wants_upload,
    },
  });

  // Best-effort notification
  context.waitUntil(sendNotification(env, entry));

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};
