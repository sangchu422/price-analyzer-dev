import { readFileSync, readdirSync } from "node:fs";
import { extname, join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const frontendRoot = resolve(process.cwd());
const sourceRoot = join(frontendRoot, "src");
const supportedExtensions = new Set([".ts", ".tsx", ".css", ".html"]);
const suspiciousMojibake =
  /\uFFFD|\u00C3|\u00C2|\u00E2(?:\u20AC|\u2122|\u0153|\u017E)|\u00EC[\u0080-\u00bf]|\u00EB[\u0080-\u00bf]|\u00EA[\u0080-\u00bf]|(?:\u5A9B|\u5BC3|\uAFB8\uC815|\uF99E|\u6E72|\uC4D6\uC774|\u6028|\uAE45)/u;

function collectSourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return collectSourceFiles(path);
    return supportedExtensions.has(extname(entry.name)) ? [path] : [];
  });
}

describe("frontend source integrity", () => {
  it("keeps every source file valid UTF-8 without mojibake markers", () => {
    const decoder = new TextDecoder("utf-8", { fatal: true });
    const files = [...collectSourceFiles(sourceRoot), join(frontendRoot, "index.html")];
    const failures: string[] = [];

    for (const file of files) {
      let source: string;
      try {
        source = decoder.decode(readFileSync(file));
      } catch {
        failures.push(`${file}: invalid UTF-8`);
        continue;
      }
      if (suspiciousMojibake.test(source)) {
        failures.push(`${file}: suspicious mojibake marker`);
      }
    }

    expect(failures).toEqual([]);
  });

  it("declares a Korean UTF-8 document shell", () => {
    const html = readFileSync(join(frontendRoot, "index.html"), "utf8");
    expect(html).toMatch(/<html lang="ko">/);
    expect(html).toMatch(/<meta charset="UTF-8"\s*\/>/);
    expect(html).toContain("<title>Price Analyzer · 신규 견적 분석</title>");
  });
});
