FROM node:18-alpine as build
WORKDIR /app

# Install dependencies and build the static site
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --silent
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
# Frontend Dockerfile (placeholder)
# Build a React frontend image. Replace with real instructions.
FROM node:18-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --silent || npm install --silent
COPY . ./
EXPOSE 3000
CMD ["npm", "run", "dev"]
