import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The API is called directly (NEXT_PUBLIC_API_URL, CORS allows the web origin) so SSE is not proxied.
};

export default nextConfig;
