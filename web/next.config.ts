import type { NextConfig } from 'next';

const config: NextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  // The API is same-origin behind Caddy in production. In `next dev` it is not,
  // so rewrite rather than introduce CORS that would not exist later.
  async rewrites() {
    const target = process.env.API_INTERNAL_URL || 'http://localhost:8000';
    return [{ source: '/api/:path*', destination: `${target}/api/:path*` }];
  },
};
export default config;
