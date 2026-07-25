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
});
