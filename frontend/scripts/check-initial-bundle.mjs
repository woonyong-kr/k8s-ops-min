import { readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import { resolve } from "node:path";

const distributionDirectory = resolve(process.cwd(), "dist");
const documentSource = await readFile(resolve(distributionDirectory, "index.html"), "utf8");
const assetUrls = [...documentSource.matchAll(/(?:href|src)="\/assets\/([^"]+)"/gu)]
  .map((match) => match[1]);
const preloadUrls = [...documentSource.matchAll(/<link[^>]+rel="modulepreload"[^>]+href="\/assets\/([^"]+)"/gu)]
  .map((match) => match[1]);
const entryUrl = documentSource.match(/<script[^>]+type="module"[^>]+src="\/assets\/([^"]+)"/u)?.[1];

if (!entryUrl) throw new Error("Initial bundle gate could not find the module entry script.");

const forbiddenInitialChunks = [
  "DesktopLocalTerminalSheet",
  "flow-",
  "motion-",
  "overlays-",
  "xterm",
  "elk",
];
const initialUrls = [entryUrl, ...preloadUrls];
const leakedChunks = initialUrls.filter((url) => (
  forbiddenInitialChunks.some((chunk) => url.toLowerCase().includes(chunk.toLowerCase()))
));

if (leakedChunks.length > 0) {
  throw new Error(`Initial bundle includes deferred product chunks: ${leakedChunks.join(", ")}`);
}

const initialBytes = await Promise.all(initialUrls.map(async (url) => (
  readFile(resolve(distributionDirectory, "assets", url))
)));
const initialGzipBytes = initialBytes.reduce((total, source) => total + gzipSync(source).byteLength, 0);
const initialGzipBudgetBytes = 650 * 1024;

if (initialGzipBytes > initialGzipBudgetBytes) {
  throw new Error(
    `Initial JavaScript gzip budget exceeded: ${initialGzipBytes} > ${initialGzipBudgetBytes} bytes.`,
  );
}

const unreferencedAssets = assetUrls.filter((url) => !initialUrls.includes(url));
console.log(JSON.stringify({
  entry: entryUrl,
  initialGzipBytes,
  initialGzipBudgetBytes,
  modulepreloads: preloadUrls,
  unreferencedAssetCount: unreferencedAssets.length,
}, null, 2));
