import type { NextConfig } from "next";

// "standalone" makes `next build` emit a minimal Node server for the Docker image; Vercel ignores it.
const nextConfig: NextConfig = { poweredByHeader: false, output: "standalone" };

export default nextConfig;
