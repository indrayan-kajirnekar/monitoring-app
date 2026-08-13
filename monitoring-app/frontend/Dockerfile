# ── Frontend Dockerfile ───────────────────────────────────────────────────────
# Stage 1 : Build the React production bundle inside Node 20
# Stage 2 : Serve it with a minimal Nginx image
#
# AJV compatibility strategy
# ───────────────────────────
# react-scripts 5 has two conflicting sub-trees that cannot coexist via normal
# package resolution:
#
#   fork-ts-checker-webpack-plugin bundles its OWN nested node_modules with
#   schema-utils@3 + ajv-keywords@3 (AJV v6 API). Those nested copies shadow
#   the top-level ajv@8 we pin, causing "Unknown keyword formatMinimum" because
#   ajv-keywords@3 calls ajv.addKeyword() which was renamed in v8.
#
# Fix:
#   1. Pin ajv@8 + ajv-keywords@5 as explicit top-level direct dependencies.
#   2. After npm install, surgically delete the entire nested node_modules
#      subtree inside fork-ts-checker-webpack-plugin. Node's module resolver
#      then walks up to /app/node_modules and finds our pinned v8 copies.
#   3. SKIP_PREFLIGHT_CHECK=true suppresses react-scripts' own AJV version
#      assertion that would otherwise abort the build.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# 1. Copy package manifest (ajv@8 + ajv-keywords@5 listed as direct deps)
COPY package.json ./

# 2. Install — legacy-peer-deps tolerates the mixed react-scripts peer tree
RUN npm install --legacy-peer-deps --loglevel=error

# 3. Nuke the ENTIRE nested node_modules inside fork-ts-checker-webpack-plugin.
#    This forces Node to resolve ajv and schema-utils upward to the top-level
#    hoisted copies (ajv@8, ajv-keywords@5) for ALL require() calls originating
#    from within that plugin.
RUN rm -rf /app/node_modules/fork-ts-checker-webpack-plugin/node_modules

# 4. Copy application source
COPY . .

# 5. Build
ENV GENERATE_SOURCEMAP=false
ENV REACT_APP_API_URL=
ENV SKIP_PREFLIGHT_CHECK=true
RUN npm run build


# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

RUN rm -rf /usr/share/nginx/html/*
COPY --from=builder /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
