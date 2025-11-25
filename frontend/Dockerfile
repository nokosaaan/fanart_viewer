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
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
# End of multi-stage build for production. Development-stage leftovers removed.
