interface ToolTimelineProps {
  events: string[];
}

export function ToolTimeline({ events }: ToolTimelineProps) {
  return (
    <section className="side-card" aria-label="Tool timeline">
      <h3>Tool timeline</h3>
      <ul className="event-list">
        {events.map((event) => (
          <li key={event}>{event}</li>
        ))}
      </ul>
    </section>
  );
}
