// TypeScript server — cross-language fixture for Phase 5.3.
import express from "express";

const app = express();

app.get("/api/users", (_req, res) => {
  res.json([]);
});

export default app;
