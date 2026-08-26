import { describe, expect, it } from "vitest";

import styles from "./styles.css?raw";

describe("responsive root styles", () => {
  it("uses the layout viewport instead of a hard minimum page width", () => {
    const rootRule = styles.match(/html,\s*body,\s*#root\s*\{([^}]*)\}/)?.[1];

    expect(rootRule).toBeDefined();
    expect(rootRule).toContain("min-width: 0");
    expect(rootRule).toContain("max-width: 100%");
    expect(rootRule).not.toMatch(/min-width:\s*320px/);
  });

  it("does not conceal genuine page overflow globally", () => {
    expect(styles).not.toMatch(
      /(?:html|body|#root)[^{]*\{[^}]*overflow-x:\s*hidden/,
    );
  });

  it("keeps glass effects out while reserving gradients for the command center", () => {
    expect(styles).not.toMatch(/backdrop-filter\s*:/);
    expect(styles).toMatch(/\.dashboard-page[\s\S]*radial-gradient\(/);
    expect(styles).toMatch(/--analysis-accent:\s*#ff0000/);
  });

  it("keeps visible focus and honors reduced motion preferences", () => {
    expect(styles).toMatch(/:focus-visible/);
    expect(styles).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  });

  it("scopes the brand tile treatment to the mark instead of the wordmark copy", () => {
    expect(styles).not.toMatch(/\.app-wordmark\s*>\s*span\s*\{/);
    expect(styles).toMatch(/\.app-wordmark\s*>\s*span:first-child\s*\{/);
  });

  it("keeps target evidence inside its row instead of covering later actions", () => {
    const popoverRule = styles.match(/\.target-evidence-popover\s*\{([^}]*)\}/)?.[1];

    expect(popoverRule).toBeDefined();
    expect(popoverRule).toContain("position: static");
    expect(popoverRule).not.toContain("position: absolute");
  });
});
