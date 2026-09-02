import { useState } from "react";
import { Alert, Button, Paper, Stack, Text, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";

export function LoginPage() {
  const queryClient = useQueryClient();
  const [reviewer, setReviewer] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await api.login(reviewer.trim(), password);
      await queryClient.invalidateQueries({ queryKey: ["me"] });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Invalid password" : "Sign-in failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <Stack align="center" justify="center" h="100vh" p="md">
      <Paper component="form" onSubmit={submit} withBorder p="xl" radius="md" w={400} maw="100%">
        <Stack>
          <Text fw={700} size="lg">
            Document Archive — Review
          </Text>
          <Text size="sm" c="dimmed">
            Sign in with your reviewer name and the shared review password. The password is stored in an
            HttpOnly cookie, not in the browser.
          </Text>
          {error ? (
            <Alert color="red" title="Could not sign in">
              {error}
            </Alert>
          ) : null}
          <TextInput
            label="Reviewer"
            required
            autoComplete="username"
            value={reviewer}
            onChange={(event) => setReviewer(event.currentTarget.value)}
          />
          <TextInput
            label="Password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.currentTarget.value)}
          />
          <Button type="submit" loading={pending} disabled={!reviewer.trim() || !password}>
            Sign in
          </Button>
        </Stack>
      </Paper>
    </Stack>
  );
}
