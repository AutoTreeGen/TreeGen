/**
 * Phase 4.12 — POST /api/waitlist proxy → parser-service /waitlist.
 *
 * Зачем proxy, а не прямой fetch с клиента в parser-service:
 *   - доменное cookie + same-origin (no CORS-preflight на каждый submit);
 *   - возможность переписать заголовки (rate-limit IP, User-Agent трим)
 *     в одном месте, когда Phase 13.x подключит Cloud Armor;
 *   - hide internal service URL — публикуем только `/api/waitlist`.
 *
 * Никаких сервер-side credentials — endpoint в parser-service публичный
 * (это лид-форма, не auth'нутые операции).
 */

import { NextResponse } from "next/server";

const PARSER_API_BASE = (
  process.env.PARSER_SERVICE_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000"
).replace(/\/$/, "");

export async function POST(request: Request): Promise<Response> {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid_json" }, { status: 400 });
  }

  // Не доверяем raw payload — отправляем строго whitelisted поля.
  if (typeof payload !== "object" || payload === null) {
    return NextResponse.json({ ok: false, error: "invalid_payload" }, { status: 400 });
  }
  const { email, locale } = payload as { email?: unknown; locale?: unknown };
  if (typeof email !== "string" || email.trim().length === 0) {
    return NextResponse.json({ ok: false, error: "invalid_email" }, { status: 400 });
  }

  const upstream = await fetch(`${PARSER_API_BASE}/waitlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      locale: typeof locale === "string" ? locale.slice(0, 16) : null,
      source: "landing",
    }),
    // 5s timeout — лид-форма не должна висеть; AbortController экспонат
    // через AbortSignal.timeout (Node 20+ standard).
    signal: AbortSignal.timeout(5000),
  });

  if (!upstream.ok) {
    return NextResponse.json({ ok: false, error: "upstream_error" }, { status: 502 });
  }
  return NextResponse.json({ ok: true });
}
