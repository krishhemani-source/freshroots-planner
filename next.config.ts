import type { NextConfig } from "next";

// Locally, /api/* is forwarded to the Python API (uvicorn on port 8000).
// On Vercel, /api/* is served by the Python function in api/index.py.
const nextConfig: NextConfig = {
  async rewrites() {
    return process.env.NODE_ENV === "development"
      ? [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }]
      : [];
  },
  experimental: {
    proxyTimeout: 600_000, // a live AI run can take a few minutes
  },
};

export default nextConfig;
