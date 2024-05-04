FROM nginx:1.25-alpine as web

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/
COPY /portal/ /web/
