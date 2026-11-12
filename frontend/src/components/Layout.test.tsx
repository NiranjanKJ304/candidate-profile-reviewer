import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SiteFooter from "./SiteFooter";
import SiteHeader from "./SiteHeader";

describe("SiteHeader", () => {
  it("renders the brand link and main navigation", () => {
    render(<SiteHeader />);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Candidate Intelligence/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
  });

  it("links to the backend API docs in a new tab without leaking the opener", () => {
    render(<SiteHeader />);
    const docs = screen.getByRole("link", { name: "API docs" });
    expect(docs).toHaveAttribute("href", "http://localhost:8000/docs");
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs).toHaveAttribute("rel", "noopener noreferrer");
  });
});

describe("SiteFooter", () => {
  it("renders the footer", () => {
    render(<SiteFooter />);
    expect(screen.getByRole("contentinfo")).toHaveTextContent("Phase 0 foundation");
  });
});
