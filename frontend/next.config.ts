import type { NextConfig } from "next";
const isDev = process.env.NODE_ENV === "development";
const nextConfig: NextConfig = { ...(isDev ? { rewrites: async () => [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }] } : { output: "export" as const }), images: { unoptimized: true } };
export default nextConfig;
