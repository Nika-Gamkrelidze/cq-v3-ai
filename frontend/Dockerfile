# The web tier: the migrated Next app and the legacy pages, in one docroot.
#
# WHY A BUILD STAGE AT ALL. Until now `web` was stock nginx with ./frontend/public bind-
# mounted, because there was nothing to build. The Next app needs compiling, and the server
# runs `docker compose up -d --build` on every push — so the build has to happen in the image
# or production would serve whatever stale output happened to be committed.
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
# Legacy first, then the export ON TOP. During the migration both are served from one root:
# a ported route wins over a same-named legacy file, which is exactly the switch-over we want
# — deleting the .html is then a separate, reversible commit.
COPY public/ /usr/share/nginx/html/
COPY --from=webbuild /build/out/ /usr/share/nginx/html/
