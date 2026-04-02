# Build stage
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

# Serve stage
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# SPA routing: redirect all requests to index.html
RUN echo 'server { \
    listen 5173; \
    root /usr/share/nginx/html; \
    index index.html; \
    resolver 127.0.0.11 valid=5s; \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    location /api { \
        set $upstream http://api:8000; \
        proxy_pass $upstream; \
    } \
    location /ws { \
        set $upstream http://api:8000; \
        proxy_pass $upstream; \
        proxy_http_version 1.1; \
        proxy_set_header Upgrade $http_upgrade; \
        proxy_set_header Connection "upgrade"; \
    } \
}' > /etc/nginx/conf.d/default.conf
EXPOSE 5173
CMD ["nginx", "-g", "daemon off;"]
