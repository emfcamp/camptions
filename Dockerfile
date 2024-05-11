FROM node:20 as client-install

WORKDIR /client/
COPY ./portal/package*.json ./
RUN chown -R node:node /client/
RUN npm install
COPY ./portal/ ./

FROM client-install as client-build
RUN npm run build

FROM nginx:1.25-alpine as web

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/
COPY --from=client-build /client/dist/ /dist/
