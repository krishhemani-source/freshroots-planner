import { NextRequest, NextResponse } from "next/server";

// Hosted only: when APP_PASSCODE is set, pages need the sign-in cookie (set by /login).
// The Python API checks the same cookie for /api/* itself.

export const config = { matcher: ["/((?!api|login|auth|_next|favicon.ico).*)"] };

export async function passcodeToken(passcode: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`freshroots:${passcode}`));
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function middleware(request: NextRequest) {
  const passcode = process.env.APP_PASSCODE;
  if (!passcode) return NextResponse.next();
  if (request.cookies.get("fr_auth")?.value === (await passcodeToken(passcode))) return NextResponse.next();
  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(login);
}
