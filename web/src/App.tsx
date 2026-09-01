import { AppShell, Group, NavLink, Text } from "@mantine/core";
import { IconFileSearch, IconFlag, IconLayoutDashboard } from "@tabler/icons-react";
import { NavLink as RouterNavLink, Route, Routes, useLocation } from "react-router-dom";
import { DashboardPage } from "./routes/DashboardPage";
import { DocumentViewPage } from "./routes/DocumentViewPage";
import { ReviewQueuePage } from "./routes/ReviewQueuePage";
import { SearchPage } from "./routes/SearchPage";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: IconLayoutDashboard },
  { to: "/review", label: "Review queue", icon: IconFlag },
  { to: "/search", label: "Search", icon: IconFileSearch },
];

function Sidebar() {
  const location = useLocation();
  return (
    <>
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          component={RouterNavLink}
          to={item.to}
          label={item.label}
          leftSection={<item.icon size={18} />}
          active={location.pathname === item.to}
        />
      ))}
    </>
  );
}

export default function App() {
  return (
    <AppShell navbar={{ width: 220, breakpoint: "sm" }} header={{ height: 56 }} padding={0}>
      <AppShell.Header>
        <Group h="100%" px="md">
          <Text fw={700}>Document Archive — Review</Text>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <Sidebar />
      </AppShell.Navbar>

      <AppShell.Main style={{ height: "calc(100vh - 56px)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <Routes>
          <Route
            path="/"
            element={
              <div className="page-scroll">
                <DashboardPage />
              </div>
            }
          />
          <Route path="/documents/:documentId" element={<DocumentViewPage />} />
          <Route
            path="/review"
            element={
              <div className="page-scroll">
                <ReviewQueuePage />
              </div>
            }
          />
          <Route
            path="/search"
            element={
              <div className="page-scroll">
                <SearchPage />
              </div>
            }
          />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
