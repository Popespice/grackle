import { describe, expect, it } from "vitest";
import { isNodeVisible } from "./matching";

const base = {
  id: "src/foo.py:Foo",
  kind: "class",
  name: "Foo",
  path: "src/foo.py",
};

const empty = {
  searchTerm: "",
  hiddenKinds: new Set<string>(),
  excludeGlobs: [],
};

describe("isNodeVisible", () => {
  it("returns true when no filters are applied", () => {
    expect(isNodeVisible(base, empty)).toBe(true);
  });

  describe("hiddenKinds", () => {
    it("hides nodes whose kind is in hiddenKinds", () => {
      expect(
        isNodeVisible(base, { ...empty, hiddenKinds: new Set(["class"]) })
      ).toBe(false);
    });

    it("shows nodes whose kind is not in hiddenKinds", () => {
      expect(
        isNodeVisible(base, { ...empty, hiddenKinds: new Set(["function"]) })
      ).toBe(true);
    });
  });

  describe("searchTerm", () => {
    it("matches on node name (case-insensitive)", () => {
      expect(isNodeVisible(base, { ...empty, searchTerm: "foo" })).toBe(true);
      expect(isNodeVisible(base, { ...empty, searchTerm: "FOO" })).toBe(true);
    });

    it("hides node when name does not match", () => {
      expect(isNodeVisible(base, { ...empty, searchTerm: "bar" })).toBe(false);
    });

    it("matches on path", () => {
      expect(isNodeVisible(base, { ...empty, searchTerm: "src" })).toBe(true);
    });

    it("matches on metadata.qualname", () => {
      const node = { ...base, metadata: { qualname: "MyModule.Foo" } };
      expect(isNodeVisible(node, { ...empty, searchTerm: "mymodule" })).toBe(
        true
      );
      expect(isNodeVisible(node, { ...empty, searchTerm: "xyz" })).toBe(false);
    });

    it("ignores metadata.qualname when absent", () => {
      expect(isNodeVisible(base, { ...empty, searchTerm: "mymodule" })).toBe(
        false
      );
    });

    it("empty searchTerm does not filter", () => {
      expect(isNodeVisible(base, { ...empty, searchTerm: "" })).toBe(true);
    });
  });

  describe("excludeGlobs", () => {
    it("excludes node whose path matches glob", () => {
      expect(isNodeVisible(base, { ...empty, excludeGlobs: ["src/**"] })).toBe(
        false
      );
    });

    it("does not exclude when glob does not match path", () => {
      expect(
        isNodeVisible(base, { ...empty, excludeGlobs: ["tests/**"] })
      ).toBe(true);
    });

    it("excludes with wildcard *.py (fnmatch-style: * crosses /)", () => {
      expect(isNodeVisible(base, { ...empty, excludeGlobs: ["*.py"] })).toBe(
        false
      );
    });

    it("skips blank glob entries", () => {
      expect(
        isNodeVisible(base, { ...empty, excludeGlobs: ["", "  ", "tests/**"] })
      ).toBe(true);
    });

    it("empty excludeGlobs does not filter", () => {
      expect(isNodeVisible(base, { ...empty, excludeGlobs: [] })).toBe(true);
    });
  });

  describe("combined filters", () => {
    it("hiddenKinds takes priority over search match", () => {
      const opts = {
        searchTerm: "foo",
        hiddenKinds: new Set(["class"]),
        excludeGlobs: [],
      };
      expect(isNodeVisible(base, opts)).toBe(false);
    });

    it("excludeGlob takes priority over search match", () => {
      const opts = {
        searchTerm: "foo",
        hiddenKinds: new Set<string>(),
        excludeGlobs: ["src/**"],
      };
      expect(isNodeVisible(base, opts)).toBe(false);
    });
  });
});
