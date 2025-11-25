FROM node:18-alpine as build
WORKDIR /app

# Install dependencies and build the static site
COPY frontend/package.json frontend/package-lock.json* ./
# Use npm ci when a lockfile exists, otherwise fall back to npm install
RUN if [ -f package-lock.json ]; then npm ci --silent; else npm install --silent; fi
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Copy entrypoint that will inject runtime env into the built site
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
EXPOSE 80
CMD ["/docker-entrypoint.sh"]
# End of multi-stage build for production. Development-stage leftovers removed.
