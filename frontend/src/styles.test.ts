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

  it("uses solid operational surfaces without decorative gradients or glass", () => {
    expect(styles).not.toMatch(/(?:linear|radial|conic)-gradient\(/);
    expect(styles).not.toMatch(/backdrop-filter\s*:/);
  });

  it("keeps visible focus and honors reduced motion preferences", () => {
    expect(styles).toMatch(/:focus-visible/);
    expect(styles).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  });

  it("scopes the brand tile treatment to the mark instead of the wordmark copy", () => {
    expect(styles).not.toMatch(/\.app-wordmark\s*>\s*span\s*\{/);
    expect(styles).toMatch(/\.app-wordmark\s*>\s*span:first-child\s*\{/);
  });
});
