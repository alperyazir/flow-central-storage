# Build stage: install dependencies and compile the Vite app
FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm install --no-audit --no-fund

COPY . .

# Build-time placeholder. The real API URL is substituted into the
# compiled JS at container startup (see docker-entrypoint.sh), so a
# single image works for every environment.
ARG VITE_API_BASE_URL=__VITE_API_BASE_URL__
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

# Runtime stage: serve the static assets with Nginx
FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Runtime API URL substitution. The image ships with the
# __VITE_API_BASE_URL__ sentinel baked into every JS bundle; the
# entrypoint sed-replaces it from the container's API_BASE_URL env
# var before nginx starts, so the same image can serve any deployment.
COPY docker-entrypoint.sh /docker-entrypoint.d/50-substitute-api-url.sh
RUN chmod +x /docker-entrypoint.d/50-substitute-api-url.sh

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
