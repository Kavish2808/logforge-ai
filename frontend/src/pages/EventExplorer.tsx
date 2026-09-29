import { useEffect, useState } from "react";
import { getEvents } from "../api/endpoints";
import { EMPTY_FILTERS, EventFilterBar, EventFilterState, toQuery } from "../components/EventFilters";
import { EventTable } from "../components/EventTable";
import { Card, CursorPager, Load } from "../components/ui";
import { num } from "../lib/format";
import { onSearch, takePendingSearch } from "../lib/search";
import { useApi } from "../lib/useApi";

const PAGE = 50;

export function EventExplorer() {
  const [filters, setFilters] = useState<EventFilterState>(() => {
    const q = takePendingSearch();
    return q ? { ...EMPTY_FILTERS, search: q } : EMPTY_FILTERS;
  });
  // Stack of cursors: [null (newest page), cursor-for-page-2, ...]. Only the current page is fetched.
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];
  const page = useApi(
    (s) => getEvents({ ...toQuery(filters), limit: PAGE, cursor, include_total: cursors.length === 1 }, s),
    [JSON.stringify(filters), cursor],
  );
  // The COUNT is requested only for the first page; keep it while paging.
  const [total, setTotal] = useState<number | null>(null);
  useEffect(() => {
    if (page.data && page.data.total !== null) setTotal(page.data.total);
  }, [page.data]);

  const applyFilters = (f: EventFilterState) => {
    setFilters(f);
    setCursors([null]);
    setTotal(null);
  };
  // The top-bar search re-targets this page while it is open.
  useEffect(() => onSearch((q) => applyFilters({ ...EMPTY_FILTERS, search: q })), []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">LOG REPOSITORY</div>
          <h1>Event Explorer</h1>
          <p>Search, filter, and inspect normalized events with complete raw lineage.</p>
        </div>
      </div>
      <Card>
        <EventFilterBar value={filters} onChange={applyFilters} />
      </Card>
      <Card title={total !== null ? `${num(total)} matching event(s)` : "Events"}>
        <Load state={page} isEmpty={(d) => d.items.length === 0} empty="No events match these filters.">
          {(d) => (
            <>
              <EventTable rows={d.items} />
              <CursorPager
                hasMore={d.has_more}
                canNewer={cursors.length > 1}
                onOlder={() => d.next_cursor && setCursors([...cursors, d.next_cursor])}
                onNewer={() => setCursors(cursors.slice(0, -1))}
                info={`Page ${cursors.length} · ${d.items.length} row(s) · newest first`}
              />
            </>
          )}
        </Load>
      </Card>
    </>
  );
}
