import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `standalone` produce un bundle minimale in `.next/standalone/`
  // usato dal `Dockerfile.frontend`. Vercel ignora questo flag.
  // Vedi docs/DEPLOY.md § 4b.
  output: "standalone",
};

export default nextConfig;
