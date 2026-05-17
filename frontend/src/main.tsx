import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { installHotkeys } from "./hotkeys";
import "./styles/index.css";

installHotkeys();

// Strip the trailing slash from BASE_URL (Vite emits "/vme/", BrowserRouter
// expects "/vme"). In dev, BASE_URL is "/" -> empty basename, which is fine.
const basename = import.meta.env.BASE_URL.replace(/\/$/, "");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter basename={basename}>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
