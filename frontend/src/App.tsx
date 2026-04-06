import { useEffect, useState } from "react";
import { OpsPage } from "./routes/OpsPage";
import { SetupPage } from "./routes/SetupPage";
import { StudioPage } from "./routes/StudioPage";

type RouteName = "setup" | "studio" | "ops";

function routeFromPath(pathname: string): RouteName {
  if (pathname.startsWith("/setup")) return "setup";
  return pathname.startsWith("/ops") ? "ops" : "studio";
}

export function App() {
  const [route, setRoute] = useState<RouteName>(() => routeFromPath(window.location.pathname));

  useEffect(() => {
    const onPop = () => setRoute(routeFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = (next: RouteName) => {
    const path = next === "ops" ? "/ops" : next === "setup" ? "/setup" : "/studio";
    window.history.pushState({}, "", path);
    setRoute(next);
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Milestone 10</p>
          <h1>DCS Dungeon Master</h1>
        </div>
        <nav className="nav">
          <button className={route === "setup" ? "tab active" : "tab"} onClick={() => navigate("setup")}>
            Setup
          </button>
          <button className={route === "studio" ? "tab active" : "tab"} onClick={() => navigate("studio")}>
            Campaign Studio
          </button>
          <button className={route === "ops" ? "tab active" : "tab"} onClick={() => navigate("ops")}>
            Live Ops
          </button>
        </nav>
      </header>
      <main className="page">{route === "setup" ? <SetupPage /> : route === "studio" ? <StudioPage /> : <OpsPage />}</main>
    </div>
  );
}
