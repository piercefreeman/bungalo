"use strict";

process.env.PORT = process.env.PORT || "3000";
process.env.HOST = "0.0.0.0";
process.env.HOSTNAME = "0.0.0.0";

const path = require("path");
const staticDir = path.join(__dirname, ".next", "static");
console.log(`[next-entry] Serving static assets from ${staticDir}`);
console.log(
  `[next-entry] Environment → PORT=${process.env.PORT} HOST=${process.env.HOST} HOSTNAME=${process.env.HOSTNAME}`
);

require("./.next/standalone/server.js");
