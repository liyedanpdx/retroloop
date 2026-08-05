import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import App from "./App";

test("renders the app heading", () => {
  render(<App />);
  expect(screen.getByText("RetroLoop")).toBeInTheDocument();
});
