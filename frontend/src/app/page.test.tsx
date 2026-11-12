import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HEALTH_OK, READY_OK, jsonResponse, mockFetch } from "@/test/fetchMock";

import Home from "./page";

function renderHome() {
  mockFetch({ "/api/v1/health": jsonResponse(HEALTH_OK), "/api/v1/ready": jsonResponse(READY_OK) });
  render(<Home />);
}

describe("Home page", () => {
  it("renders the heading, capabilities and the status panel", async () => {
    renderHome();
    expect(screen.getByRole("heading", { level: 1, name: "Candidate Intelligence" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Capabilities" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "System status" })).toBeInTheDocument();
    expect(await screen.findByText("All systems operational")).toBeInTheDocument();
  });

  it("explains the four evaluation steps in order", () => {
    renderHome();
    expect(screen.getByRole("heading", { name: "How an evaluation works" })).toBeInTheDocument();
    const steps = screen.getAllByText(/^Step \d$/).map((el) => el.textContent);
    expect(steps).toEqual(["Step 1", "Step 2", "Step 3", "Step 4"]);
  });

  it("marks resume and job description analysis as available and links to them", () => {
    renderHome();
    expect(screen.getAllByText("Available")).toHaveLength(2);
    expect(screen.getAllByText("Planned")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Resume analysis" })).toHaveAttribute("href", "/resumes");
    expect(screen.getByRole("link", { name: "Job description analysis" })).toHaveAttribute(
      "href",
      "/job-descriptions",
    );
  });

  it("does not present planned features as active", () => {
    renderHome();
    const card = screen.getByText("Evidence-based evaluation").closest("li");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByText("Planned")).toBeInTheDocument();
    expect(within(card as HTMLElement).queryByRole("link")).toBeNull();
  });
});
