/** @type {import('next').NextConfig} */
const nextConfig = {
  /* STATIC EXPORT, deliberately.
     `next build` emits plain HTML/JS/CSS that the existing nginx serves exactly as it serves
     today's files — no Node process in production. That matters here: the server already runs
     Postgres+pgvector, a ~2 GB embeddings model and a torch sidecar, and an SSR process would
     be a seventh service competing for the same RAM. Nothing in this app needs a server of its
     own: the backend is FastAPI and every session lives in the browser. */
  output: 'export',

  /* One .html per route (`/usage` -> out/usage.html) rather than a directory per route. It
     keeps the exported tree flat enough to drop alongside the legacy pages in one docroot
     during the migration, and nginx's `try_files $uri $uri.html` serves it at a clean URL. */
  trailingSlash: false,

  /* The default image loader needs a server. Nothing here uses next/image yet; this makes the
     failure a build error rather than a broken image in production. */
  images: { unoptimized: true },

  reactStrictMode: true,
};

export default nextConfig;
