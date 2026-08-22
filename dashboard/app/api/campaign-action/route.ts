import { NextResponse } from "next/server";

/**
 * Proxy for mutating campaign calls.
 *
 * The admin token lives here, on the server, and never enters the browser
 * bundle. A NEXT_PUBLIC_ variable would be readable by anyone who opens
 * devtools, which would make the token pointless.
 */
const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  const { campaignId, action } = await request.json();

  if (!["start", "pause", "abort"].includes(action)) {
    return NextResponse.json({ detail: "unknown action" }, { status: 400 });
  }

  const res = await fetch(`${BASE}/api/campaigns/${campaignId}/${action}`, {
    method: "POST",
    headers: { "X-Admin-Token": process.env.CONVOY_ADMIN_API_TOKEN ?? "" },
  });

  return NextResponse.json(await res.json(), { status: res.status });
}