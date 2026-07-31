import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

const CONFIGS = Object.freeze([
  {
    label: "production image",
    path: new URL("../nginx.conf", import.meta.url),
  },
  {
    label: "development console",
    path: new URL("../../deploy/management/console-dev.yaml", import.meta.url),
  },
]);

function exactLocation(config, pathname) {
  const escaped = pathname.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  return new RegExp(`location\\s*=\\s*${escaped}\\s*\\{(?<body>[\\s\\S]*?)\\n\\s*\\}`, "gu");
}

describe("canonical delivery route configuration", () => {
  it.each(CONFIGS)("enforces the policy in the $label server", async ({ path }) => {
    const config = await readFile(path, "utf8");

    for (const alias of ["/devpreview-unified.html", "/devpreview-index.html"]) {
      const matches = [...config.matchAll(exactLocation(config, alias))];
      expect(matches, `${alias} must have exactly one exact location`).toHaveLength(1);
      expect(matches[0].groups?.body).toMatch(/absolute_redirect\s+off;/u);
      expect(matches[0].groups?.body).toMatch(/return\s+308\s+\/;/u);
    }

    const rootMatches = [...config.matchAll(exactLocation(config, "/"))];
    expect(rootMatches, "canonical root must have one exact location").toHaveLength(1);
    expect(rootMatches[0].groups?.body).toMatch(/try_files\s+\/index[.]html\s+=404;/u);
    expect(config).toMatch(/location\s+~\*\s+\[[.]\]html\$\s*\{\s*return\s+404;/u);
    expect(config).toMatch(/location\s+\/\s*\{[\s\S]*?try_files\s+\$uri\s+\/index[.]html;/u);
  });

});
