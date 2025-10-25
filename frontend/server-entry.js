"use strict";

const fs = require("fs");
const path = require("path");

process.env.PORT = process.env.PORT || "3000";
process.env.HOST = "0.0.0.0";
process.env.HOSTNAME = "0.0.0.0";

const standaloneDir = __dirname;
const projectRoot = path.resolve(standaloneDir, "..");
const rootNextDir = path.join(projectRoot, ".next");
const localNextDir = path.join(standaloneDir, ".next");
const localStaticDir = path.join(localNextDir, "static");

try {
  if (!fs.existsSync(localNextDir)) {
    fs.symlinkSync(rootNextDir, localNextDir, "dir");
    console.log(`[next-entry] Created symlink ${localNextDir} -> ${rootNextDir}`);
  }
} catch (err) {
  console.warn(`[next-entry] Failed to prepare .next symlink: ${err.message}`);
}

console.log(
  `[next-entry] Environment → PORT=${process.env.PORT} HOST=${process.env.HOST} HOSTNAME=${process.env.HOSTNAME}`
);
console.log(`[next-entry] Static assets root: ${localStaticDir}`);

require("./.next/standalone/server.js");
