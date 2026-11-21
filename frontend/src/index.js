import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { initErrorReporting } from "@/lib/errorReporting";

// Only when REACT_APP_SENTRY_DSN is set.
initErrorReporting();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
