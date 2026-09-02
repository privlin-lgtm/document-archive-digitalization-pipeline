import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Card, FileButton, Group, Loader, Paper, SimpleGrid, Stack, Table, Text } from "@mantine/core";
import { IconUpload } from "@tabler/icons-react";
import { useStats } from "../hooks/useStats";
import { useDocuments, useUploadDocuments } from "../hooks/useDocuments";
import { OffsetPager } from "../components/OffsetPager";

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <Card withBorder padding="md" radius="sm">
      <Text size="xs" c="dimmed" tt="uppercase">
        {label}
      </Text>
      <Text size="xl" fw={700}>
        {value}
      </Text>
    </Card>
  );
}

const PAGE_SIZE = 20;

export function DashboardPage() {
  const [offset, setOffset] = useState(0);
  const { data: stats } = useStats();
  const { data: documents, isLoading } = useDocuments({ limit: PAGE_SIZE, offset });
  const uploadDocuments = useUploadDocuments();
  const resetRef = useRef<() => void>(null);
  const navigate = useNavigate();

  function handleFiles(files: File[]) {
    if (files.length === 0) return;
    uploadDocuments.mutate(files, { onSuccess: () => resetRef.current?.() });
  }

  return (
    <Stack p="md" gap="lg">
      <Group justify="space-between">
        <Text fw={700} size="lg">
          Dashboard
        </Text>
        <FileButton resetRef={resetRef} onChange={handleFiles} accept="image/*" multiple>
          {(props) => (
            <Button {...props} leftSection={<IconUpload size={16} />} loading={uploadDocuments.isPending}>
              Upload scans
            </Button>
          )}
        </FileButton>
      </Group>

      {stats && (
        <SimpleGrid cols={{ base: 2, sm: 4 }}>
          <StatCard label="Total documents" value={stats.total_documents} />
          <StatCard label="Indexed" value={stats.documents_indexed} />
          <StatCard label="Needing review" value={stats.documents_needing_review} />
          <StatCard label="Open flags" value={stats.open_review_flags} />
        </SimpleGrid>
      )}

      <Paper withBorder p="md" radius="sm">
        <Text fw={600} mb="sm">
          Recent documents
        </Text>
        {isLoading ? (
          <Loader size="sm" />
        ) : (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Filename</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Uploaded</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {documents?.results.map((doc) => (
                <Table.Tr key={doc.id} onClick={() => navigate(`/documents/${doc.id}`)} style={{ cursor: "pointer" }}>
                  <Table.Td>{doc.filename}</Table.Td>
                  <Table.Td>
                    <Badge variant="light">{doc.status}</Badge>
                  </Table.Td>
                  <Table.Td>{new Date(doc.upload_time).toLocaleString()}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {documents ? (
          <OffsetPager total={documents.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
        ) : null}
      </Paper>
    </Stack>
  );
}
