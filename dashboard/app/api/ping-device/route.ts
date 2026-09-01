import { NextResponse } from "next/server";

// Same pattern as campaign-action: the admin token lives server-side only.
const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  const { deviceId } = await request.json();
  const res = await fetch(`${BASE}/api/devices/${deviceId}/ping`, {
    method: "POST",
    headers: { "X-Admin-Token": process.env.CONVOY_ADMIN_API_TOKEN ?? "" },
  });
  return NextResponse.json(await res.json(), { status: res.status });
}