import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createApiClient } from "@/lib/apiClient";
import { jsonResponse, mockFetch } from "@/test/fetchMock";
import { CID, LC_NOT_FOUND, LC_STATUS, LC_STATUS_DISABLED, lcProfile } from "@/test/leetcodeFixtures";

import LeetCodeSection from "./LeetCodeSection";

const api = createApiClient("http://api.test");
const LC = `/candidates/${CID}/leetcode`;
const STATUS = "/integrations/leetcode/status";

function lastInit(fetchFn: { mock: { calls: unknown[][] } }): RequestInit | undefined {
  return (fetchFn.mock.calls.at(-1) as [string, RequestInit] | undefined)?.[1];
}

describe("LeetCodeSection", () => {
  it("explains why nothing is collected and cites the sources", async () => {
    mockFetch({ [STATUS]: jsonResponse(LC_STATUS), [LC]: jsonResponse(LC_NOT_FOUND, 404) });
    render(<LeetCodeSection api={api} candidateId={CID} />);
    const note = (await screen.findAllByRole("note"))[0];
    expect(note).toHaveTextContent("no official or permitted data API");
    expect(note).toHaveTextContent("self-reported and not verified");
    expect(await within(note).findByRole("link", { name: "https://leetcode.com/terms/" })).toBeInTheDocument();
    expect(screen.getByTestId("leetcode-mode")).toHaveTextContent("LeetCode is never contacted");
    expect(screen.queryByRole("button", { name: /sync/i })).not.toBeInTheDocument();
  });

  it("links a profile, then saves self-reported evidence", async () => {
    let state: "none" | "linked" | "evidence" = "none";
    const fetchFn = mockFetch({
      [STATUS]: jsonResponse(LC_STATUS),
      [`${LC}/evidence`]: () => {
        state = "evidence";
        return jsonResponse(lcProfile());
      },
      [LC]: () => {
        if (lastInit(fetchFn)?.method === "POST") {
          state = "linked";
          return jsonResponse(lcProfile({ evidence: null }), 201);
        }
        return state === "none" ? jsonResponse(LC_NOT_FOUND, 404) : jsonResponse(lcProfile({ evidence: null }));
      },
    });
    render(<LeetCodeSection api={api} candidateId={CID} />);
    fireEvent.change(await screen.findByLabelText("LeetCode username or profile URL"), { target: { value: "https://leetcode.com/u/jane_doe/" } });
    fireEvent.click(screen.getByRole("button", { name: "Link" }));

    expect(await screen.findByTestId("leetcode-platform-data")).toHaveTextContent("not available (no permitted API)");
    expect(screen.getByText(/not checked against LeetCode/)).toBeInTheDocument();
    expect(screen.getByText(/Missing evidence is not evidence of missing skills/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add self-reported evidence" }));
    const form = screen.getByRole("form", { name: "Self-reported LeetCode evidence" });
    fireEvent.change(within(form).getByLabelText("Reported by"), { target: { value: "candidate" } });
    fireEvent.change(within(form).getByLabelText("Total solved"), { target: { value: "300" } });
    fireEvent.change(within(form).getByLabelText(/^Languages/), { target: { value: "Python3: 250" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save self-reported evidence" }));

    const view = await screen.findByTestId("leetcode-evidence");
    expect(within(view).getByText("Self-reported · not verified")).toBeInTheDocument();
    expect(within(view).getByText(/Reported by the candidate/)).toBeInTheDocument();
    expect(within(view).getByText("300")).toBeInTheDocument();
    expect(within(view).getAllByText("not provided (unknown, not zero)").length).toBeGreaterThan(0);
    expect(within(view).getByRole("list", { name: "Contest history" })).toHaveTextContent("Weekly Contest 400");
    expect(state).toBe("evidence");
    const body = JSON.parse(String(lastInit(fetchFn)?.body ?? "{}")) as Record<string, unknown>;
    expect(body).toEqual({ reported_by: "candidate", total_solved: 300, languages: [{ name: "Python3", problems_solved: 250 }] });
  });

  it("validates the evidence form on the client", async () => {
    const fetchFn = mockFetch({ [STATUS]: jsonResponse(LC_STATUS), [LC]: jsonResponse(lcProfile({ evidence: null })) });
    render(<LeetCodeSection api={api} candidateId={CID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add self-reported evidence" }));
    fireEvent.click(screen.getByRole("button", { name: "Save self-reported evidence" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Choose who is reporting");
    expect(alert).toHaveTextContent("at least one evidence value");
    expect(fetchFn.mock.calls.some((c) => ((c as unknown[])[1] as RequestInit | undefined)?.method === "PUT")).toBe(false);
  });

  it("shows server validation details", async () => {
    mockFetch({
      [STATUS]: jsonResponse(LC_STATUS),
      [`${LC}/evidence`]: jsonResponse(
        { error: { code: "validation_error", message: "Request validation failed", details: [{ loc: ["body"], msg: "Value error, contest history has more entries than contests attended", type: "value_error" }] } },
        422,
      ),
      [LC]: jsonResponse(lcProfile({ evidence: null })),
    });
    render(<LeetCodeSection api={api} candidateId={CID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add self-reported evidence" }));
    fireEvent.change(screen.getByLabelText("Reported by"), { target: { value: "candidate" } });
    fireEvent.change(screen.getByLabelText("Contests attended"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save self-reported evidence" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("more entries than contests attended");
  });

  it("edits existing evidence with a pre-filled form and flags stale evidence", async () => {
    mockFetch({
      [STATUS]: jsonResponse(LC_STATUS),
      [LC]: jsonResponse(lcProfile({ freshness: { submitted_at: null, as_of: "2026-01-01", age_days: 300, stale: true, stale_after_days: 30 } })),
    });
    render(<LeetCodeSection api={api} candidateId={CID} />);
    expect(await screen.findByText(/older than 30 days/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit evidence" }));
    expect(screen.getByLabelText("Total solved")).toHaveValue("300");
    expect(screen.getByLabelText("Contest rating")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByTestId("leetcode-evidence")).toBeInTheDocument();
  });

  it("clears evidence and removes the profile", async () => {
    const fetchFn = mockFetch({
      [STATUS]: jsonResponse(LC_STATUS),
      [`${LC}/evidence`]: () => new Response(null, { status: 204 }),
      [LC]: () => (lastInit(fetchFn)?.method === "DELETE" ? new Response(null, { status: 204 }) : jsonResponse(lcProfile())),
    });
    render(<LeetCodeSection api={api} candidateId={CID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Clear evidence" }));
    expect(await screen.findByText(/No self-reported evidence yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByLabelText("LeetCode username or profile URL")).toBeInTheDocument();
  });

  it("explains that the integration is disabled", async () => {
    mockFetch({ [STATUS]: jsonResponse(LC_STATUS_DISABLED), [LC]: jsonResponse(LC_NOT_FOUND, 404) });
    render(<LeetCodeSection api={api} candidateId={CID} />);
    expect(await screen.findByText(/Add leetcode to ENABLED_PLATFORMS/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Link" })).toBeDisabled();
  });
});
