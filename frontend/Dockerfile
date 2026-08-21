# --- Build the Angular bundle ---------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# --- Serve via nginx -------------------------------------------------------
FROM nginx:alpine

# nginx's entrypoint renders templates/*.template with envsubst at startup.
# Both variables need a value in the environment — envsubst only replaces
# variables that are *set*, so an unset one would be left in the config
# verbatim and nginx would refuse to start. Hence the defaults here rather
# than in the template.
ENV PORT=80
# host:port that /api/ and /ws/ proxy to, as seen from *inside* this container
# (the container name on the deployment network).
ENV API_UPSTREAM=jp-conversation-practice-backend:8000

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html

EXPOSE 80
