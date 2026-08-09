# The frontend image: a dev server, a build, and an nginx that serves it (#18).
#
# `_docs/decisions.md`, "Multi-stage frontend Dockerfile". The `prod` stage
# copies `dist` and the nginx config and nothing else — no `node_modules`, no
# source, no dev server, and no `.env`, which `.dockerignore` keeps out of the
# context entirely.
#
# `npm ci` rather than `npm install`, in both stages: the committed lockfile is
# what makes the image reproducible.

FROM node:22-alpine AS deps

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci


FROM deps AS dev

COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]


FROM deps AS build

COPY . .
RUN npm run build


FROM nginx:alpine AS prod

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
