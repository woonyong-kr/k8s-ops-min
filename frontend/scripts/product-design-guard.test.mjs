import { execFile } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { afterEach, describe, expect, it } from "vitest";

const execFileAsync = promisify(execFile);
const scriptPath = resolve(dirname(fileURLToPath(import.meta.url)), "product-design-guard.mjs");
const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { force: true, recursive: true })));
});

describe("product-design-guard Motion import ownership", () => {
  it("rejects Motion package imports outside the Motion boundary", async () => {
    const result = await runGuard({
      "features/Surface.tsx": "import { m } from 'motion/react-m'; export const Surface = () => null;",
    });

    expect(result.exitCode).toBe(1);
    expect(result.output).toContain("[motion-import-ownership]");
    expect(result.output).toContain("Motion package imports are allowed only under src/motion.");
  });

  it("allows Motion package imports in the Motion boundary", async () => {
    const result = await runGuard({
      "motion/RefreshGlyph.tsx": "import { LazyMotion } from 'motion/react'; export const RefreshGlyph = () => LazyMotion;",
    });

    expect(result.exitCode).toBe(0);
    expect(result.output).not.toContain("motion-import-ownership");
  });
});

async function runGuard(files) {
  const root = await mkdtemp(resolve(tmpdir(), "opsia-design-guard-"));
  temporaryRoots.push(root);

  await Promise.all(Object.entries(files).map(async ([relativePath, source]) => {
    const filePath = resolve(root, relativePath);
    await mkdir(dirname(filePath), { recursive: true });
    await writeFile(filePath, source);
  }));

  try {
    const { stderr, stdout } = await execFileAsync("node", [scriptPath], {
      env: { ...process.env, PRODUCT_DESIGN_GUARD_ROOT: root },
    });
    return { exitCode: 0, output: `${stdout}${stderr}` };
  } catch (error) {
    return {
      exitCode: error.code ?? 1,
      output: `${error.stdout ?? ""}${error.stderr ?? ""}`,
    };
  }
}
