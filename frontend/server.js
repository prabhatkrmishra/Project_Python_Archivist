const express = require("express");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.static(path.join(__dirname, "public")));

app.listen(PORT, () => {
  console.log(`Archivist frontend: http://localhost:${PORT}`);
  console.log(`Make sure the API is running at http://127.0.0.1:8000`);
});
