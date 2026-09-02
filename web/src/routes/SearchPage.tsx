import { useState } from "react";
import { Link } from "react-router-dom";
import { Group, Loader, NumberInput, Paper, Select, Stack, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconSearch } from "@tabler/icons-react";
import { useSearch } from "../hooks/useSearch";
import { SnippetHighlight } from "../components/SnippetHighlight";
import { OffsetPager } from "../components/OffsetPager";
import type { EntityType } from "../api/types";

const PAGE_SIZE = 20;

const ENTITY_TYPE_OPTIONS = [
  { value: "person", label: "Person" },
  { value: "date", label: "Date" },
  { value: "location", label: "Location" },
  { value: "amount", label: "Amount" },
];

export function SearchPage() {
  const [q, setQ] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [entityType, setEntityType] = useState<string | null>(null);
  const [location, setLocation] = useState("");
  const [minConfidence, setMinConfidence] = useState<number | string>("");
  const [offset, setOffset] = useState(0);
  const [debouncedQ] = useDebouncedValue(q, 250);

  const { data, isFetching } = useSearch({
    q: debouncedQ,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    entity_type: (entityType as EntityType) || undefined,
    location: location || undefined,
    min_confidence: minConfidence === "" ? undefined : Number(minConfidence),
    limit: PAGE_SIZE,
    offset,
  });

  return (
    <Stack p="md" gap="md">
      <TextInput
        placeholder="Search document text…"
        leftSection={<IconSearch size={16} />}
        value={q}
        onChange={(event) => {
          setQ(event.currentTarget.value);
          setOffset(0);
        }}
        size="md"
        autoFocus
      />

      <Group grow>
        <TextInput label="Date from" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.currentTarget.value)} />
        <TextInput label="Date to" type="date" value={dateTo} onChange={(e) => setDateTo(e.currentTarget.value)} />
        <Select
          label="Entity type"
          data={ENTITY_TYPE_OPTIONS}
          value={entityType}
          onChange={setEntityType}
          clearable
        />
        <TextInput label="Location" value={location} onChange={(e) => setLocation(e.currentTarget.value)} />
        <NumberInput
          label="Min OCR confidence"
          min={0}
          max={100}
          value={minConfidence}
          onChange={setMinConfidence}
        />
      </Group>

      {isFetching && <Loader size="sm" />}

      {data && (
        <Stack gap="xs">
          <Text size="sm" c="dimmed">
            {data.total} result{data.total === 1 ? "" : "s"}
          </Text>
          {data.results.map((result) => (
            <Paper
              key={result.page_id}
              withBorder
              p="sm"
              radius="sm"
              className="search-hit"
              component={Link}
              to={`/documents/${result.document_id}`}
            >
              <Group justify="space-between">
                <Text size="sm" fw={500}>
                  {result.filename} — page {result.page_number}
                </Text>
                <Text size="xs" c="dimmed">
                  rank {result.rank.toFixed(3)}
                </Text>
              </Group>
              <Text size="sm" mt={4}>
                <SnippetHighlight snippet={result.snippet} />
              </Text>
            </Paper>
          ))}
          <OffsetPager total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
        </Stack>
      )}
    </Stack>
  );
}
