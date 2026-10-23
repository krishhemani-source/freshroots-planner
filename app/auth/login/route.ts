import { NextRequest, NextResponse } from "next/server";

import { passcodeToken } from "@/middleware";

// Checks the team passcode and sets the sign-in cookie (30 days).
export async function POST(request: NextRequest) {
  const form = await request.formData();
  const passcode = process.env.APP_PASSCODE ?? "";
  const next = String(form.get("next") || "/");
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  if (!passcode || form.get("passcode") !== passcode) {
    return NextResponse.redirect(new URL(`/login?error=1&next=${encodeURIComponent(safeNext)}`, request.url), 303);
  }
  const response = NextResponse.redirect(new URL(safeNext, request.url), 303);
  response.cookies.set("fr_auth", await passcodeToken(passcode), {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: 60 * 60 * 24 * 30,
  });
  return response;
}
