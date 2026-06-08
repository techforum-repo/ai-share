import express from "express";
import cors from "cors";
import dotenv from "dotenv";

dotenv.config();

const app = express();
app.use(cors());

app.get("/token", (req, res) => {
  // For internal/local testing: return a fixed token from env.
  // Better long-term: generate a short-lived search token per user.
  res.json({ token: process.env.COVEO_TOKEN });
});

app.listen(process.env.PORT || 3001, () => {
  console.log(`Token server running on http://localhost:${process.env.PORT || 3001}`);
});