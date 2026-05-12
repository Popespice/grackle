module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "scope-enum": [
      2,
      "always",
      ["agent", "frontend", "shared-types", "docs", "ci", "tooling", "release"],
    ],
    "scope-empty": [2, "never"],
  },
};
