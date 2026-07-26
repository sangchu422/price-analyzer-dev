import { readFileSync, readdirSync } from "node:fs";
import { extname, join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const frontendRoot = resolve(process.cwd());
const sourceRoot = join(frontendRoot, "src");
const supportedExtensions = new Set([".ts", ".tsx", ".css", ".html"]);
const mojibakeMarkers = [
  [0x5360, 0xc465, 0xc637],
  [0xf9cf],
  [0x5bc3, 0x044a],
  [0x003f, 0xc496, 0xc73c],
  [0xfffd],
  [0x5a9b],
  [0x5bc3],
  [0xafb8, 0xc815],
  [0xf99e],
  [0x6e72],
  [0xc4d6, 0xc774],
  [0x6028],
  [0xae45],
].map((points) => String.fromCodePoint(...points));
const malformedEncodingPattern =
  /\u00C3|\u00C2|\u00E2(?:\u20AC|\u2122|\u0153|\u017E)|\u00EC[\u0080-\u00bf]|\u00EB[\u0080-\u00bf]|\u00EA[\u0080-\u00bf]/u;

function containsSuspiciousMojibake(source: string) {
  return (
    mojibakeMarkers.some((marker) => source.includes(marker)) ||
    malformedEncodingPattern.test(source)
  );
}

function collectSourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return collectSourceFiles(path);
    return supportedExtensions.has(extname(entry.name)) ? [path] : [];
  });
}

describe("frontend source integrity", () => {
  it("detects every known Korean mojibake marker and accepts clean Korean", () => {
    const requiredMarkers = [
      [0x5360, 0xc465, 0xc637],
      [0xf9cf],
      [0x5bc3, 0x044a],
      [0x003f, 0xc496, 0xc73c],
      [0xfffd],
    ].map((points) => String.fromCodePoint(...points));

    for (const marker of requiredMarkers) {
      expect(containsSuspiciousMojibake(`앞${marker}뒤`)).toBe(true);
    }
    expect(containsSuspiciousMojibake("정상적인 신규 견적 분석 화면")).toBe(
      false,
    );
  });

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
      if (containsSuspiciousMojibake(source)) {
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
