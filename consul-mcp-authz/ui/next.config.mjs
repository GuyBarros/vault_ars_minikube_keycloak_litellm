import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  // Pin Next's file-trace root to this app's own folder; the repo has
  // sibling lockfiles (web-app, etc.) so Next would otherwise climb to a
  // higher ancestor and bundle unrelated files.
  outputFileTracingRoot: __dirname,
};

export default nextConfig;
