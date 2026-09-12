# Two stages: build the SPA with node, serve the static output with nginx. The runtime
# image has no node in it and nothing to attack beyond nginx and a directory of files.
FROM node:22-alpine AS build

WORKDIR /app

# Dependencies first, so a source change does not reinstall node_modules.
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY proxy-common.inc /etc/nginx/conf.d/proxy-common.inc
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
