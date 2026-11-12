import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createApiClient } from "@/lib/apiClient";
import { CF_NOT_FOUND, CF_STATUS_DISABLED, CF_STATUS_ENABLED, CID, SNAPSHOT, cfProfile } from "@/test/codeforcesFixtures";
import { jsonResponse, mockFetch } from "@/test/fetchMock";

import CodeforcesSection from "./CodeforcesSection";

const api = createApiClient("http://api.test");
const CF = `/candidates/${CID}/codeforces`;
const STATUS = "/integrations/codeforces/status";

function sequence(...items: (() => Response)[]) {
  let i = 0;
  return () => items[Math.min(i++, items.length - 1)]();
}

describe("CodeforcesSection", () => {
  it("connects a handle, polls and shows rating, analytics, contests and submissions", async () => {
    const fetchFn = mockFetch({
      [STATUS]: jsonResponse(CF_STATUS_ENABLED),
      [CF]: () => {
        const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
        if (call[1]?.method === "POST") return jsonResponse(cfProfile({ sync_status: "pending", data: null, collected_at: null }), 202);
        return getSeq();
      },
    });
    const getSeq = sequence(
      () => jsonResponse(CF_NOT_FOUND, 404),
      () => jsonResponse(cfProfile({ sync_status: "processing", data: null, collected_at: null })),
      () => jsonResponse(cfProfile()),
    );
    render(<CodeforcesSection api={api} candidateId={CID} pollIntervalMs={5} />);

    fireEvent.change(await screen.findByLabelText("Codeforces handle or profile URL"), {
      target: { value: "https://codeforces.com/profile/algo_dev" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    const data = await screen.findByTestId("codeforces-data");
    expect(screen.getByTestId("codeforces-sync-status")).toHaveTextContent("Synchronised");
    expect(screen.getByText(/Ownership not verified/)).toBeInTheDocument();
    expect(within(data).getByText("1650 / 1920")).toBeInTheDocument();
    expect(within(data).getByText("expert / candidate master")).toBeInTheDocument();
    expect(within(data).getByText("Example University")).toBeInTheDocument();
    expect(within(data).getByText(/not a complete measure of programming ability/)).toBeInTheDocument();
    expect(within(data).getByTestId("codeforces-window")).toHaveTextContent("6 most recent submissions");
    expect(within(data).getByTestId("codeforces-window")).toHaveTextContent("do not cover the whole history");
    expect(within(data).getByText("of 4 attempted")).toBeInTheDocument();
    expect(within(within(data).getByRole("list", { name: "Difficulty distribution" })).getByText("Unrated")).toBeInTheDocument();
    expect(within(data).getByRole("list", { name: "Topic distribution" })).toHaveTextContent("dp (1)");
    expect(within(data).getByRole("list", { name: "Language distribution" })).toHaveTextContent("Python 3 (4)");
    expect(within(data).getByRole("link", { name: "Codeforces Round 2 (Div. 2)" })).toHaveAttribute("href", "https://codeforces.com/contest/1010");
    expect(within(data).getByText("(-50)")).toBeInTheDocument();
    expect(within(data).getAllByRole("link", { name: "C. Coins" })[0]).toHaveAttribute("href", "https://codeforces.com/contest/1010/problem/C");
    expect(within(data).getByText("2026-10: 6")).toBeInTheDocument();
    expect(screen.getByText(/Only the most recent 6 submissions/)).toBeInTheDocument();
    expect(screen.getByTestId("codeforces-integration")).toHaveTextContent("at most 1 request per 2 s");
  });

  it("validates input on the client", async () => {
    const fetchFn = mockFetch({ [STATUS]: jsonResponse(CF_STATUS_ENABLED), [CF]: jsonResponse(CF_NOT_FOUND, 404) });
    render(<CodeforcesSection api={api} candidateId={CID} />);
    fireEvent.change(await screen.findByLabelText("Codeforces handle or profile URL"), { target: { value: "tour ist" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("spaces");
    expect(fetchFn.mock.calls.some((c) => ((c as unknown[])[1] as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("shows the server's validation message", async () => {
    const fetchFn = mockFetch({
      [STATUS]: jsonResponse(CF_STATUS_ENABLED),
      [CF]: () => {
        const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
        return call[1]?.method === "POST"
          ? jsonResponse({ error: { code: "not_a_profile", message: "This is not a profile URL" } }, 422)
          : jsonResponse(CF_NOT_FOUND, 404);
      },
    });
    render(<CodeforcesSection api={api} candidateId={CID} />);
    fireEvent.change(await screen.findByLabelText("Codeforces handle or profile URL"), {
      target: { value: "https://codeforces.com/contest/1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This is not a profile URL");
  });

  it("explains a failed sync, keeps previous data and retries via /retry", async () => {
    const fetchFn = mockFetch({
      [STATUS]: jsonResponse(CF_STATUS_ENABLED),
      [`${CF}/retry`]: jsonResponse(cfProfile({ sync_status: "pending" }), 202),
      [CF]: sequence(
        () => jsonResponse(cfProfile({ sync_status: "failed", sync_error_code: "rate_limited", sync_error: "Codeforces API call limit exceeded" })),
        () => jsonResponse(cfProfile()),
      ),
    });
    render(<CodeforcesSection api={api} candidateId={CID} pollIntervalMs={5} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("call limit was reached");
    expect(alert).toHaveTextContent("Showing data from the last successful synchronisation");
    expect(screen.getByTestId("codeforces-data")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry sync" }));
    expect(await screen.findByText(/^Synchronised/)).toBeInTheDocument();
    expect(fetchFn.mock.calls.some(([u]) => String(u).endsWith("/codeforces/retry"))).toBe(true);
  });

  it("explains unavailable submissions instead of showing zeros", async () => {
    mockFetch({
      [STATUS]: jsonResponse(CF_STATUS_ENABLED),
      [CF]: jsonResponse(
        cfProfile({
          data: {
            ...SNAPSHOT,
            submissions: { ...SNAPSHOT.submissions, status: "network_error", note: "Submissions could not be collected", items: [] },
            analytics: {
              ...SNAPSHOT.analytics,
              status: "network_error",
              note: "Submission statistics are unavailable because submissions could not be collected; this is not the same as zero activity",
              submissions: null,
            },
          },
        }),
      ),
    });
    render(<CodeforcesSection api={api} candidateId={CID} />);
    const data = await screen.findByTestId("codeforces-data");
    expect(within(data).getByTestId("codeforces-window")).toHaveTextContent("not the same as zero activity");
    expect(within(data).queryByText("Solved unique problems")).not.toBeInTheDocument();
    expect(within(data).getByText("Submissions could not be collected")).toBeInTheDocument();
  });

  it("flags stale data", async () => {
    mockFetch({
      [STATUS]: jsonResponse(CF_STATUS_ENABLED),
      [CF]: jsonResponse(cfProfile({ freshness: { collected_at: "2026-01-01T00:00:00Z", age_seconds: 9e6, stale: true, stale_after_hours: 168 } })),
    });
    render(<CodeforcesSection api={api} candidateId={CID} />);
    expect(await screen.findByText(/older than 7 days/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });

  it("removes the association", async () => {
    let removed = false;
    const fetchFn = mockFetch({
      [STATUS]: jsonResponse(CF_STATUS_ENABLED),
      [CF]: () => {
        const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
        if (call[1]?.method === "DELETE") return new Response(null, { status: 204 });
        return removed ? jsonResponse(CF_NOT_FOUND, 404) : jsonResponse(cfProfile());
      },
    });
    render(<CodeforcesSection api={api} candidateId={CID} />);
    const button = await screen.findByRole("button", { name: "Remove" });
    removed = true;
    fireEvent.click(button);
    expect(await screen.findByLabelText("Codeforces handle or profile URL")).toBeInTheDocument();
  });

  it("explains that the integration is disabled", async () => {
    mockFetch({ [STATUS]: jsonResponse(CF_STATUS_DISABLED), [CF]: jsonResponse(CF_NOT_FOUND, 404) });
    render(<CodeforcesSection api={api} candidateId={CID} />);
    expect(await screen.findByRole("note")).toHaveTextContent("ENABLED_PLATFORMS");
    expect(await screen.findByRole("button", { name: "Connect" })).toBeDisabled();
  });
});
