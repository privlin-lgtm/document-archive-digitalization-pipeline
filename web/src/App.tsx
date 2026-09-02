import { AppShell, Burger, Button, Group, Loader, NavLink, Text } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconFileSearch, IconFlag, IconLayoutDashboard } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink as RouterNavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import { DashboardPage } from "./routes/DashboardPage";
import { DocumentViewPage } from "./routes/DocumentViewPage";
import { LoginPage } from "./routes/LoginPage";
import { ReviewQueuePage } from "./routes/ReviewQueuePage";
import { SearchPage } from "./routes/SearchPage";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: IconLayoutDashboard },
  { to: "/review", label: "Review queue", icon: IconFlag },
  { to: "/search", label: "Search", icon: IconFileSearch },
];

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
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
          onClick={onNavigate}
        />
      ))}
    </>
  );
}

function AuthenticatedApp({ reviewer }: { reviewer: string }) {
  const queryClient = useQueryClient();
  // Mobile nav starts closed; the burger (shown only below the "sm"
  // breakpoint) toggles it, and picking a page closes it again -- without
  // this, AppShell's navbar has no collapse state at all below the
  // breakpoint and just renders as a permanent full-width block, hiding
  // every page's content behind it (reproduced: verified at 375px width).
  const [mobileNavOpened, { toggle: toggleMobileNav, close: closeMobileNav }] = useDisclosure(false);

  async function logout() {
    await api.logout();
    queryClient.clear();
  }

  return (
    <AppShell
      navbar={{ width: 220, breakpoint: "sm", collapsed: { mobile: !mobileNavOpened } }}
      header={{ height: 56 }}
      padding={0}
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={mobileNavOpened} onClick={toggleMobileNav} hiddenFrom="sm" size="sm" />
            <Text fw={700}>Document Archive — Review</Text>
          </Group>
          <Group gap="sm">
            <Text size="sm" c="dimmed">
              {reviewer}
            </Text>
            <Button size="xs" variant="light" onClick={logout}>
              Sign out
            </Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <Sidebar onNavigate={closeMobileNav} />
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

export default function App() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    retry: false,
  });

  if (isLoading) {
    return (
      <Group h="100vh" justify="center">
        <Loader />
      </Group>
    );
  }
  if (isError || !data) {
    return <LoginPage />;
  }
  return <AuthenticatedApp reviewer={data.reviewer} />;
}
