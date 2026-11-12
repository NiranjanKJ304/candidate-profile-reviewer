import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import CandidatesWorkspace from "@/components/candidate/CandidatesWorkspace";
import { createApiClient } from "@/lib/apiClient";
import { jsonResponse, mockFetch } from "@/test/fetchMock";
import { CF_NOT_FOUND, CF_STATUS_ENABLED } from "@/test/codeforcesFixtures";
import { LC_NOT_FOUND, LC_STATUS } from "@/test/leetcodeFixtures";
import { CID, NOT_FOUND, STATUS_DISABLED, STATUS_ENABLED, profile } from "@/test/githubFixtures";

import GitHubSection from "./GitHubSection";

const BASE = "http://api.test";
const api = createApiClient(BASE);
const GH = `/candidates/${CID}/github`;

function sequence(...items: (() => Response)[]) {
  let i = 0;
  return () => items[Math.min(i++, items.length - 1)]();
}

describe("GitHubSection", () => {
  it("connects a profile, polls and shows summary, repositories, languages and explanations", async () => {
    const fetchFn = mockFetch({
      "/integrations/github/status": jsonResponse(STATUS_ENABLED),
      [GH]: () => {
        const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
        if (call[1]?.method === "POST") return jsonResponse(profile({ sync_status: "pending", data: null, collected_at: null }), 202);
        return getSeq();
      },
    });
    const getSeq = sequence(
      () => jsonResponse(NOT_FOUND, 404),
      () => jsonResponse(profile({ sync_status: "processing", data: null, collected_at: null })),
      () => jsonResponse(profile()),
    );
    render(<GitHubSection api={api} candidateId={CID} pollIntervalMs={5} />);

    fireEvent.change(await screen.findByLabelText("GitHub username or profile URL"), {
      target: { value: "https://github.com/octo-dev" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    const data = await screen.findByTestId("github-data");
    expect(screen.getByTestId("github-sync-status")).toHaveTextContent("Synchronised");
    expect(screen.getByText(/Ownership not verified/)).toBeInTheDocument();
    expect(within(data).getByText("Example Corp")).toBeInTheDocument();
    expect(within(data).getByRole("link", { name: "data-api" })).toHaveAttribute("href", "https://github.com/octo-dev/data-api");
    expect(within(data).getByText("fork")).toBeInTheDocument();
    expect(within(data).getByText(/Python/, { selector: "li" })).toBeInTheDocument();
    expect(within(data).getByText("No topics set.")).toBeInTheDocument();
    expect(within(data).getByText("Rate limited")).toBeInTheDocument();
    expect(within(data).getByText(/describe visibility, not coding ability/)).toBeInTheDocument();
    expect(screen.getByText(/Collected without GITHUB_TOKEN/)).toBeInTheDocument();
    expect(screen.getByTestId("github-integration")).toHaveTextContent("52 requests left");
  });

  it("validates input on the client", async () => {
    const fetchFn = mockFetch({
      "/integrations/github/status": jsonResponse(STATUS_ENABLED),
      [GH]: jsonResponse(NOT_FOUND, 404),
    });
    render(<GitHubSection api={api} candidateId={CID} />);
    fireEvent.change(await screen.findByLabelText("GitHub username or profile URL"), { target: { value: "octo cat" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("spaces");
    expect(fetchFn.mock.calls.some((c) => ((c as unknown[])[1] as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("shows the server's validation message", async () => {
    const fetchFn = mockFetch({
      "/integrations/github/status": jsonResponse(STATUS_ENABLED),
      [GH]: () => {
        const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
        return call[1]?.method === "POST"
          ? jsonResponse({ error: { code: "unsupported_domain", message: "Only github.com profile URLs are accepted" } }, 422)
          : jsonResponse(NOT_FOUND, 404);
      },
    });
    render(<GitHubSection api={api} candidateId={CID} />);
    fireEvent.change(await screen.findByLabelText("GitHub username or profile URL"), { target: { value: "https://gitlab.com/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only github.com profile URLs are accepted");
  });

  it("explains a failed sync and keeps the previous data", async () => {
    mockFetch({
      "/integrations/github/status": jsonResponse(STATUS_ENABLED),
      [GH]: jsonResponse(profile({ sync_status: "failed", sync_error_code: "rate_limited", sync_error: "GitHub API rate limit exhausted" })),
    });
    render(<GitHubSection api={api} candidateId={CID} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("rate limit was reached");
    expect(alert).toHaveTextContent("Showing data from the last successful synchronisation");
    expect(screen.getByTestId("github-data")).toBeInTheDocument();
  });

  it("retries synchronisation", async () => {
    const fetchFn = mockFetch({
      "/integrations/github/status": jsonResponse(STATUS_ENABLED),
      [`${GH}/sync`]: jsonResponse(profile({ sync_status: "pending" }), 202),
      [GH]: sequence(
        () => jsonResponse(profile({ sync_status: "failed", sync_error_code: "network_error" })),
        () => jsonResponse(profile()),
      ),
    });
    render(<GitHubSection api={api} candidateId={CID} pollIntervalMs={5} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry sync" }));
    expect(await screen.findByText(/^Synchronised/)).toBeInTheDocument();
    expect(fetchFn.mock.calls.some(([u]) => String(u).endsWith("/github/sync"))).toBe(true);
  });

  it("removes the association", async () => {
    const fetchFn = mockFetch({
      "/integrations/github/status": jsonResponse(STATUS_ENABLED),
      [GH]: () => {
        const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
        if (call[1]?.method === "DELETE") return new Response(null, { status: 204 });
        return removed ? jsonResponse(NOT_FOUND, 404) : jsonResponse(profile());
      },
    });
    let removed = false;
    render(<GitHubSection api={api} candidateId={CID} />);
    const button = await screen.findByRole("button", { name: "Remove" });
    removed = true;
    fireEvent.click(button);
    expect(await screen.findByLabelText("GitHub username or profile URL")).toBeInTheDocument();
  });

  it("explains that the integration is disabled", async () => {
    mockFetch({ "/integrations/github/status": jsonResponse(STATUS_DISABLED), [GH]: jsonResponse(NOT_FOUND, 404) });
    render(<GitHubSection api={api} candidateId={CID} />);
    expect(await screen.findByRole("note")).toHaveTextContent("ENABLED_PLATFORMS=github");
    expect(await screen.findByRole("button", { name: "Connect" })).toBeDisabled();
  });
});

describe("CandidatesWorkspace", () => {
  it("creates a candidate and opens its GitHub and Codeforces sections", async () => {
    mockFetch({
      "/candidates?limit=100&offset=0": sequence(
        () => jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }),
        () => jsonResponse({ items: [{ id: CID, full_name: "Jordan Sample", email: null }], total: 1, limit: 100, offset: 0 }),
      ),
      "/api/v1/candidates": jsonResponse({ id: CID, full_name: "Jordan Sample", email: null }, 201),
      "/integrations/github/status": jsonResponse(STATUS_ENABLED),
      [GH]: jsonResponse(NOT_FOUND, 404),
      "/integrations/codeforces/status": jsonResponse(CF_STATUS_ENABLED),
      [`/candidates/${CID}/codeforces`]: jsonResponse(CF_NOT_FOUND, 404),
      "/integrations/leetcode/status": jsonResponse(LC_STATUS),
      [`/candidates/${CID}/leetcode`]: jsonResponse(LC_NOT_FOUND, 404),
    });
    render(<CandidatesWorkspace apiBaseUrl={BASE} />);
    expect(await screen.findByText("No candidates yet.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("New candidate name"), { target: { value: "Jordan Sample" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("button", { name: "Jordan Sample" })).toBeInTheDocument();
    expect(await screen.findByLabelText("GitHub username or profile URL")).toBeInTheDocument();
    expect(await screen.findByLabelText("Codeforces handle or profile URL")).toBeInTheDocument();
    expect(await screen.findByLabelText("LeetCode username or profile URL")).toBeInTheDocument();
  });

  it("reports a configuration error", () => {
    render(<CandidatesWorkspace apiBaseUrl={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("NEXT_PUBLIC_API_BASE_URL");
  });
});
