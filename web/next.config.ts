import type { NextConfig } from "next";
import { apiUrl } from "./scripts/root-env";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // A separate output folder lets a production build run beside a live `next dev` (which owns .next).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The API is called directly (NEXT_PUBLIC_API_URL, CORS allows the web origin) so SSE is not proxied.
  // The URL is resolved once in scripts/root-env.ts (the environment, else the repo-root .env) and
  // baked in here, so `npm run dev` on its own calls the same API as `make web`.
  env: { NEXT_PUBLIC_API_URL: apiUrl },
  // No framework badge over the application: the dev server is what the product is shown from.
  devIndicators: false,
};

export default nextConfig;
