import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { AnimatedNumber } from "./AnimatedNumber";

it("keeps an accessible final value while animating its visible digits", () => {
  const { rerender } = render(<AnimatedNumber value={7672} suffix="건" />);

  expect(screen.getByLabelText("7,672건")).toBeInTheDocument();
  expect(document.querySelector(".t-digit-group.is-animating")).toBeInTheDocument();

  rerender(<AnimatedNumber value={8120} suffix="건" />);
  expect(screen.getByLabelText("8,120건")).toBeInTheDocument();
});
