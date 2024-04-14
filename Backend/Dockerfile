FROM node:20 as backend

WORKDIR /backend/

COPY ./package*.json ./
RUN npm install
COPY ./ ./

CMD ["npx", "tsx", "watch", "src/index.ts"]