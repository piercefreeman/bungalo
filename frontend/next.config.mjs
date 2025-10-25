import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  webpack: (config) => {
    config.resolve = config.resolve || {};
    config.resolve.alias = config.resolve.alias || {};
    const root = resolve(projectRoot);
    config.resolve.alias["@"] = root;
    config.resolve.alias["@/"] = `${root}/`;
    return config;
  },
};

export default nextConfig;
