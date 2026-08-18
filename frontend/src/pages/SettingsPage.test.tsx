import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

afterEach(() => vi.unstubAllGlobals());

it("shows a disabled notice and no input when the admin has not enabled hChat", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/settings/hchat")) {
        return jsonResponse({ enabled: false, has_key: false });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );

  renderApp("/settings");

  expect(
    await screen.findByText("관리자가 이 기능을 활성화하지 않았습니다."),
  ).toBeVisible();
  expect(screen.queryByLabelText("hChat API 키")).not.toBeInTheDocument();
});

it("shows an unset badge and lets the user save a new key", async () => {
  const requests: Array<{ url: string; method: string; body?: unknown }> = [];
  let hasKey = false;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({
        url,
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (url.includes("/api/settings/hchat") && (init?.method ?? "GET") === "PUT") {
        hasKey = true;
        return jsonResponse({ enabled: true, has_key: hasKey });
      }
      if (url.includes("/api/settings/hchat")) {
        return jsonResponse({ enabled: true, has_key: hasKey });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );

  renderApp("/settings");

  expect(await screen.findByText("미설정")).toBeVisible();
  await userEvent.type(
    screen.getByLabelText("hChat API 키"),
    "sk-personal-123",
  );
  await userEvent.click(screen.getByRole("button", { name: "저장" }));

  expect(await screen.findByText("설정됨")).toBeVisible();
  expect(screen.getByText("hChat 키를 저장했습니다.")).toBeVisible();
  const put = requests.find((request) => request.method === "PUT");
  expect(put?.body).toEqual({ api_key: "sk-personal-123" });
});
