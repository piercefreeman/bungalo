"use strict";

const fs = require("fs");
const path = require("path");

process.env.PORT = process.env.PORT || "8000";
process.env.HOST = "0.0.0.0";
process.env.HOSTNAME = "0.0.0.0";

const rootDir = __dirname;
const staticDir = path.join(rootDir, ".next", "standalone", ".next", "static");

if (!fs.existsSync(staticDir)) {
  console.warn(
    `[next-entry] Expected static assets at ${staticDir} but directory is missing`
  );
}

console.log(
  `[next-entry] Environment → PORT=${process.env.PORT} HOST=${process.env.HOST} HOSTNAME=${process.env.HOSTNAME}`
);
console.log(`[next-entry] Static assets root: ${staticDir}`);

require("./.next/standalone/server.js");
