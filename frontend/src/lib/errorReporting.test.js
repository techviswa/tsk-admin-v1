import { scrubEvent } from "./errorReporting";

test("AdminCore error reports carry no personal data or secrets", () => {
  const event = scrubEvent({
    user: { email: "admin@example.com" },
    request: { url: "https://admin.example.com/reset?token=abc", data: "x" },
    message: "Failed for owner@example.com 9845012345 Bearer abc.def",
    breadcrumbs: [{ category: "console", message: "x" }, { category: "xhr", data: { url: "/api/users?email=a@b.c", method: "GET" } }],
    extra: { body: "secret" },
  });
  expect(event.user).toBeUndefined();
  expect(event.extra).toBeUndefined();
  expect(event.request).toEqual({ url: "https://admin.example.com/reset" });
  expect(event.message).toBe("Failed for [email] [number] Bearer [redacted]");
  expect(event.breadcrumbs).toEqual([{ category: "xhr", message: undefined, data: { url: "/api/users", method: "GET", status_code: undefined } }]);
});
