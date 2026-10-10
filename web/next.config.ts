import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // A separate output folder lets a production build run beside a live `next dev` (which owns .next).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The API is called directly (NEXT_PUBLIC_API_URL, CORS allows the web origin) so SSE is not proxied.
};

export default nextConfig;
