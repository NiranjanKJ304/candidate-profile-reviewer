import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HEALTH_OK, READY_OK, READY_PG_DOWN, jsonResponse, mockFetch } from "@/test/fetchMock";

import StatusPanel, { buildRows } from "./StatusPanel";

const BASE = "http://api.test";

function row(key: string) {
  return within(screen.getByTestId(`status-${key}`));
}

describe("StatusPanel", () => {
  it("shows all systems operational when backend and dependencies are healthy", async () => {
    mockFetch({ "/api/v1/health": jsonResponse(HEALTH_OK), "/api/v1/ready": jsonResponse(READY_OK) });
    render(<StatusPanel apiBaseUrl={BASE} />);

    expect(await screen.findByText("All systems operational")).toBeInTheDocument();
    expect(row("api").getByText("Online")).toBeInTheDocument();
    expect(row("api").getByText("v0.1.0 · test")).toBeInTheDocument();
    expect(row("postgres").getByText("Connected")).toBeInTheDocument();
    expect(row("postgres").getByText("1.3 ms")).toBeInTheDocument();
    expect(row("redis").getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText(BASE)).toBeInTheDocument();
  });

  it("shows a degraded state with the failing dependency", async () => {
    mockFetch({ "/api/v1/health": jsonResponse(HEALTH_OK), "/api/v1/ready": jsonResponse(READY_PG_DOWN, 503) });
    render(<StatusPanel apiBaseUrl={BASE} />);

    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(row("postgres").getByText("Unavailable")).toBeInTheDocument();
    expect(row("postgres").getByText("ConnectionRefusedError")).toBeInTheDocument();
    expect(row("redis").getByText("Connected")).toBeInTheDocument();
  });

  it("reports dependencies as Unknown (not down) when the backend is unreachable", async () => {
    mockFetch({ "/api/v1/health": new TypeError("Failed to fetch"), "/api/v1/ready": new TypeError("Failed to fetch") });
    render(<StatusPanel apiBaseUrl={BASE} />);

    expect(await screen.findByText("Backend unavailable")).toBeInTheDocument();
    expect(row("api").getByText("Unreachable")).toBeInTheDocument();
    expect(row("postgres").getByText("Unknown")).toBeInTheDocument();
    expect(row("redis").getByText("Unknown")).toBeInTheDocument();
    expect(screen.queryByText("Unavailable")).not.toBeInTheDocument();
  });

  it("flags an invalid backend response", async () => {
    mockFetch({
      "/api/v1/health": jsonResponse({ status: "ok" }),
      "/api/v1/ready": jsonResponse({ nonsense: true }),
    });
    render(<StatusPanel apiBaseUrl={BASE} />);

    expect(await screen.findByText("Backend unavailable")).toBeInTheDocument();
    expect(row("api").getByText("Invalid response")).toBeInTheDocument();
    expect(row("postgres").getByText("Unknown")).toBeInTheDocument();
  });

  it("re-checks status when Refresh is clicked", async () => {
    let ready: unknown = READY_PG_DOWN;
    const fetchFn = mockFetch({
      "/api/v1/health": jsonResponse(HEALTH_OK),
      "/api/v1/ready": () => jsonResponse(ready, ready === READY_OK ? 200 : 503),
    });
    render(<StatusPanel apiBaseUrl={BASE} />);
    expect(await screen.findByText("Degraded")).toBeInTheDocument();

    ready = READY_OK;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("All systems operational")).toBeInTheDocument();
    expect(fetchFn).toHaveBeenCalledTimes(4);
  });

  it("shows a configuration error and makes no requests when the base URL is invalid", () => {
    const fetchFn = mockFetch({});
    render(<StatusPanel apiBaseUrl={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("NEXT_PUBLIC_API_BASE_URL");
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("shows a loading state before responses arrive", async () => {
    mockFetch({
      "/api/v1/health": () => new Promise<Response>(() => {}),
      "/api/v1/ready": () => new Promise<Response>(() => {}),
    });
    render(<StatusPanel apiBaseUrl={BASE} />);
    await waitFor(() => expect(screen.getByTestId("overall-status")).toHaveTextContent("Checking"));
    expect(screen.getByRole("button")).toBeDisabled();
  });
});

describe("buildRows", () => {
  it("marks a dependency missing from the readiness payload as not reported", () => {
    const rows = buildRows({
      health: { kind: "ok", data: { ...HEALTH_OK, status: "ok" }, httpStatus: 200, requestId: null },
      readiness: {
        kind: "ok",
        data: { status: "ready", checks: {}, timestamp: READY_OK.timestamp },
        httpStatus: 200,
        requestId: null,
      },
      checkedAt: new Date(),
    });
    expect(rows.find((r) => r.key === "redis")).toMatchObject({ tone: "unknown", value: "Not reported" });
  });

  it("marks a timed-out dependency as a warning", () => {
    const rows = buildRows({
      health: { kind: "ok", data: { ...HEALTH_OK, status: "ok" }, httpStatus: 200, requestId: null },
      readiness: {
        kind: "ok",
        data: {
          status: "not_ready",
          checks: { postgres: { status: "timeout", latencyMs: null, error: "timed out after 2.0s" } },
          timestamp: READY_OK.timestamp,
        },
        httpStatus: 503,
        requestId: null,
      },
      checkedAt: new Date(),
    });
    expect(rows.find((r) => r.key === "postgres")).toMatchObject({
      tone: "warn",
      value: "Timed out",
      detail: "timed out after 2.0s",
    });
  });
});
