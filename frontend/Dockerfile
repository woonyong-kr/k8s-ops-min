# 콘솔 정적 서빙 + same-origin API 프록시.
# AWS CD는 frontend/를 직접 이미지로 빌드하므로 Dockerfile이 Vite 빌드까지 책임진다.
FROM node:22-alpine AS build
WORKDIR /app
ARG SOURCE_SHA
ENV VITE_SOURCE_SHA=${SOURCE_SHA}

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/
EXPOSE 8080
