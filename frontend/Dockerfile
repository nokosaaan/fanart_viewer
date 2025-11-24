# Frontend Dockerfile (placeholder)
# Build a React frontend image. Replace with real instructions.
FROM node:18-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --silent || npm install --silent
COPY . ./
EXPOSE 3000
CMD ["npm", "run", "dev"]
