# The web tier: the Next app, compiled to static files, plus the static assets it links to.
#
# WHY A BUILD STAGE AT ALL. `web` used to be stock nginx with ./frontend/public bind-mounted,
# because there was nothing to build. The Next app needs compiling, and the server runs
# `docker compose up -d --build` on every push — so the build has to happen in the image or
# production would serve whatever stale output happened to be committed.
#
# WHY STATIC. `next build` with output:'export' emits plain files. No Node process runs in
# production: the box already hosts Postgres+pgvector, a ~2 GB embeddings model and a torch
# sidecar, and an SSR server would be a seventh service competing for that RAM for no gain —
# the backend is FastAPI and every session lives in the browser.

FROM node:20-alpine AS webbuild
WORKDIR /build
# Dependencies first, so a change to a page does not re-download the world.
COPY next/package.json next/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY next/ ./
RUN npm run build

FROM nginx:alpine
# Two sources, one docroot. `public/` is now ASSETS ONLY — the vanilla .html pages and their
# shared modules were deleted at cutover, so nothing in it can collide with the export and
# the copy order no longer decides which stack wins a filename. What is left is what the
# exported pages link to by absolute path and Next therefore never emits: the favicon and the
# two logos (components/Header.tsx), the KB import templates under guides/ (workspace/kb/
# Import.tsx links /guides/templates/kb-template-*.…), and the operator/tenant guide docs.
#
# They live HERE rather than in next/public because they are served, not bundled: dropping
# them into the Next tree would add a build step to changing a PDF. Retired URLs
# (/tenant.html, /admin.html, /kb-admin.html, /copilot-demo.html) are 301s in deploy/nginx.conf
# and deploy/tls-ssl.conf, not files.
COPY public/ /usr/share/nginx/html/
COPY --from=webbuild /build/out/ /usr/share/nginx/html/
