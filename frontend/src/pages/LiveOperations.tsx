import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  AutopilotCustomer,
  AutopilotMonitor,
  AutopilotRow,
  AutopilotStage,
  AutopilotTask,
} from "../api/types";
import { RiskBadge } from "../components/RiskBadge";
import {
  Badge,
  EmptyState,
  ErrorState,
  Loading,
  PageHeader,
  StatCard,
} from "../components/ui";
import { useResource } from "../hooks/useResource";
import { errorMessage, formatDateTime, formatMoney } from "../lib/format";

type Tone = "ok" | "warn" | "error" | "info" | "neutral";

const STAGE: Record<
  AutopilotStage,
  { label: string; tone: Tone; busy?: boolean }
> = {
  queued: { label: "Queued", tone: "info", busy: true },
  analysing: { label: "Analysing", tone: "info", busy: true },
  deciding: { label: "Deciding", tone: "info", busy: true },
  writing: { label: "Writing email", tone: "info", busy: true },
  awaiting_reply: { label: "Awaiting reply", tone: "neutral" },
  escalated: { label: "With an officer", tone: "warn" },
  waiting: { label: "No contact now", tone: "neutral" },
  resolved: { label: "Resolved", tone: "ok" },
  failed: { label: "Failed", tone: "error" },
};

const AGENT: Record<AutopilotTask["kind"], string> = {
  analyse: "Analysis Agent",
  decide: "Decision Agent",
  converse: "Conversation Agent",
  escalate: "Escalation",
};

const HEALTH: Record<
  AutopilotMonitor["health"],
  { label: string; tone: Tone }
> = {
  ok: { label: "Running", tone: "ok" },
  degraded: { label: "Running with failures", tone: "warn" },
  down: { label: "Workers not running", tone: "error" },
  off: { label: "Autopilot off", tone: "neutral" },
};

function StageBadge({ stage }: { stage: AutopilotStage }) {
  const s = STAGE[stage];
  return (
    <span className={`badge badge-${s.tone}`}>
      {s.busy ? (
        <span className="spinner spinner-sm" aria-hidden="true" />
      ) : (
        <span className="dot" aria-hidden="true" />
      )}
      {s.label}
    </span>
  );
}

/** Reloads whenever the server says something changed; falls back to polling if the stream drops. */
function useLiveStream(onChange: () => void): boolean {
  const [connected, setConnected] = useState(false);
  const latest = useRef(onChange);
  useEffect(() => {
    latest.current = onChange;
  });
  useEffect(() => {
    const source = api.autopilotStream();
    source.addEventListener("change", () => latest.current());
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false); // the browser reconnects by itself
    const poll = setInterval(() => {
      if (source.readyState !== EventSource.OPEN) latest.current();
    }, 10000);
    return () => {
      clearInterval(poll);
      source.close();
    };
  }, []);
  return connected;
}

export function LiveOperations() {
  const overview = useResource(api.autopilot);
  const [open, setOpen] = useState<number | null>(null);
  const [detail, setDetail] = useState<AutopilotCustomer | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const loadDetail = useCallback((id: number) => {
    api.autopilotCustomer(id).then(
      (d) => {
        setDetail(d);
        setDetailError(null);
      },
      (e) => setDetailError(errorMessage(e)),
    );
  }, []);

  const reload = useCallback(() => {
    void overview.refresh();
    if (open !== null) loadDetail(open);
  }, [overview, open, loadDetail]);
  const connected = useLiveStream(reload);

  const toggle = (id: number) => {
    if (open === id) {
      setOpen(null);
      return;
    }
    setOpen(id);
    setDetail(null);
    loadDetail(id);
  };

  if (overview.loading) return <Loading label="Loading live operations…" />;
  if (overview.error || !overview.data)
    return (
      <ErrorState
        error={overview.error ?? new Error("No data")}
        onRetry={overview.refresh}
      />
    );
  const { monitor, customers } = overview.data;

  return (
    <>
      <PageHeader
        eyebrow="Autopilot"
        title="Live operations"
        description="Overdue customers are picked up automatically and handled by the agents in the background. This page updates by itself, and nothing stops when you close it."
        actions={
          <span className={`badge badge-${connected ? "ok" : "warn"}`}>
            <span
              className={connected ? "dot live-dot" : "dot"}
              aria-hidden="true"
            />
            {connected ? "Live" : "Reconnecting…"}
          </span>
        }
      />
      <MonitorPanel monitor={monitor} onRetried={reload} />

      <section className="card">
        <h2>Customers</h2>
        {customers.length === 0 ? (
          <EmptyState title="No customers yet">
            {monitor.enabled
              ? "The autopilot picks up overdue customers within a few seconds."
              : "Set AUTOPILOT_ENABLED=true in backend/.env and restart the backend."}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table live-table">
              <thead>
                <tr>
                  <th>Customer</th>
                  <th className="num">Outstanding</th>
                  <th>Overdue</th>
                  <th>Risk</th>
                  <th>Status</th>
                  <th>Agent now</th>
                  <th>Plan</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((c) => (
                  <Fragment key={c.customer_id}>
                    <CustomerRow
                      row={c}
                      open={open === c.customer_id}
                      onToggle={() => toggle(c.customer_id)}
                    />
                    {open === c.customer_id ? (
                      <tr className="live-expand">
                        <td colSpan={8}>
                          {detailError ? (
                            <p className="field-error">{detailError}</p>
                          ) : detail ? (
                            <CustomerDetail detail={detail} />
                          ) : (
                            <Loading label="Loading the timeline…" />
                          )}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function MonitorPanel({
  monitor,
  onRetried,
}: {
  monitor: AutopilotMonitor;
  onRetried: () => void;
}) {
  const health = HEALTH[monitor.health];
  const inFlight = [
    "queued",
    "analysing",
    "deciding",
    "writing",
    "awaiting_reply",
  ].reduce((n, s) => n + (monitor.stages[s as AutopilotStage] ?? 0), 0);
  const [busy, setBusy] = useState<number | null>(null);
  const retry = (id: number) => {
    setBusy(id);
    api.retryTask(id).finally(() => {
      setBusy(null);
      onRetried();
    });
  };
  return (
    <>
      <section className="grid grid-4">
        <StatCard
          label="In progress"
          value={inFlight}
          hint={`of at most ${monitor.max_cases} at once`}
          tone="info"
        />
        <StatCard
          label="With an officer"
          value={monitor.stages.escalated ?? 0}
          hint={`${monitor.today.escalations} escalated today`}
          tone="warn"
        />
        <StatCard
          label="Emails sent today"
          value={monitor.today.emails_sent}
          hint={`${monitor.today.replies} replies received`}
          tone="ok"
        />
        <StatCard
          label="Failed tasks"
          value={monitor.tasks.failed}
          hint={`${monitor.tasks.pending} pending · ${monitor.tasks.processing} running`}
          tone={monitor.tasks.failed ? "error" : "neutral"}
        />
      </section>

      <section className="card">
        <div className="live-monitor-head">
          <h2>Monitor Agent</h2>
          <Badge tone={health.tone}>{health.label}</Badge>
        </div>
        <p className="muted small">
          {monitor.workers.filter((w) => !w.worker.endsWith("-watch")).length}{" "}
          worker(s), {monitor.busy_workers} busy · {monitor.tasks.done} tasks
          done · emails{" "}
          {monitor.auto_send
            ? "sent automatically after the compliance checks"
            : "wait for an officer’s approval"}
          {monitor.oldest_pending
            ? ` · oldest waiting task since ${formatDateTime(monitor.oldest_pending)}`
            : ""}
        </p>
        {monitor.processing.length ? (
          <ul className="live-list">
            {monitor.processing.map((t) => (
              <li key={t.id}>
                <span className="spinner spinner-sm" aria-hidden="true" />{" "}
                {AGENT[t.kind]} on customer #{t.customer_id}
                <span className="muted small">
                  {" "}
                  · since {formatDateTime(t.started_at)}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {monitor.failed.length ? (
          <>
            <h3>Failed tasks</h3>
            <ul className="live-list">
              {monitor.failed.map((t) => (
                <li key={t.id}>
                  <Badge tone="error">{AGENT[t.kind]}</Badge> customer #
                  {t.customer_id}: {t.error}
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={busy === t.id}
                    onClick={() => retry(t.id)}
                  >
                    Retry
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </section>
    </>
  );
}

function CustomerRow({
  row,
  open,
  onToggle,
}: {
  row: AutopilotRow;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <tr
      className={open ? "live-row live-row-open" : "live-row"}
      onClick={onToggle}
    >
      <td>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-expanded={open}
          onClick={(e) => (e.stopPropagation(), onToggle())}
        >
          {open ? "▾" : "▸"} {row.name ?? `#${row.customer_id}`}
        </button>
        <div className="muted small">{row.email ?? "no email"}</div>
      </td>
      <td className="num">{formatMoney(row.outstanding)}</td>
      <td>
        {row.months_late
          ? `${row.months_late} month${row.months_late > 1 ? "s" : ""}`
          : "—"}
      </td>
      <td>
        <RiskBadge
          level={row.risk_level}
          probability={row.default_probability}
        />
      </td>
      <td>
        <StageBadge stage={row.stage} />
        {row.note ? (
          <div className="muted small live-note">{row.note}</div>
        ) : null}
      </td>
      <td>
        {row.task ? (
          <span
            className={
              row.task.status === "processing"
                ? "live-agent live-agent-busy"
                : "live-agent"
            }
          >
            {AGENT[row.task.kind]}
            <span className="muted small">
              {" "}
              · {row.task.status === "processing" ? "working" : "next"}
            </span>
          </span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>
        {row.plan ? (
          <PlanText plan={row.plan} />
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="muted small">{formatDateTime(row.updated_at)}</td>
    </tr>
  );
}

function PlanText({ plan }: { plan: NonNullable<AutopilotRow["plan"]> }) {
  return (
    <span className="small">
      {plan.next === "email"
        ? `Email, ${plan.tone} tone`
        : plan.next === "call"
          ? "Call"
          : plan.next === "escalate"
            ? "Escalate"
            : "Wait"}
      {plan.hardship_offer ? " · hardship plan" : ""}
    </span>
  );
}

function CustomerDetail({ detail }: { detail: AutopilotCustomer }) {
  const { context: ctx, emails, timeline } = detail;
  const a = ctx.analysis;
  const d = ctx.decision;
  return (
    <div className="live-detail">
      {detail.summary?.length ? (
        <section className="live-summary">
          <h3>
            Summary{detail.name ? `: ${detail.name}` : ""}
            {detail.email ? (
              <span className="muted small"> · {detail.email}</span>
            ) : null}
          </h3>
          <ul>
            {detail.summary.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </section>
      ) : null}
      <div className="grid grid-2">
        <div>
          <h3>Analysis</h3>
          {a ? (
            <>
              <p>{a.analysis.summary}</p>
              <p className="small">
                <Badge
                  tone={
                    a.prediction_check.verdict === "disagrees"
                      ? "warn"
                      : "neutral"
                  }
                >
                  Prediction {a.prediction_check.verdict.replace("_", " ")}
                </Badge>{" "}
                {a.prediction_check.note}
              </p>
              <p className="muted small">
                {a.analysis.situation.replace(/_/g, " ")} · {a.analysis.urgency}{" "}
                urgency · {a.communication.emails_sent} email(s),{" "}
                {a.communication.replies} reply(ies),{" "}
                {a.communication.broken_promises} broken promise(s)
              </p>
            </>
          ) : (
            <p className="muted">Not analysed yet.</p>
          )}
        </div>
        <div>
          <h3>Decision</h3>
          {d ? (
            <>
              <p>
                <PlanText plan={d.plan} />{" "}
                <span className="muted small">(after {d.trigger})</span>
              </p>
              <ul className="small">
                {d.recommendation.reasons.map((r, i) => (
                  <li key={i}>{r.text}</li>
                ))}
              </ul>
              {d.sources.length ? (
                <p className="muted small">
                  Policies:{" "}
                  {d.sources
                    .map((s) => s.filename)
                    .filter((f, i, all) => all.indexOf(f) === i)
                    .join(", ")}
                </p>
              ) : null}
            </>
          ) : (
            <p className="muted">No decision yet.</p>
          )}
          {ctx.escalation ? (
            <p className="small">
              <Badge tone="warn">
                {ctx.escalation.kind === "call" ? "Call task" : "Escalated"}
              </Badge>{" "}
              {ctx.escalation.reason}{" "}
              <span className="muted">
                (
                {ctx.escalation.notified.length
                  ? `notified ${ctx.escalation.notified.join(", ")}`
                  : "no officer to notify"}
                )
              </span>
            </p>
          ) : null}
        </div>
      </div>

      {emails.length ? (
        <>
          <h3>Emails</h3>
          <ul className="live-emails">
            {emails.map((m) => (
              <li
                key={m.id}
                className={
                  m.direction === "inbound"
                    ? "live-email live-email-in"
                    : "live-email"
                }
              >
                <div className="small">
                  <strong>
                    {m.direction === "inbound" ? "Customer" : "ComplyAgent"}
                  </strong>{" "}
                  · {m.subject ?? "(no subject)"} ·{" "}
                  <span className="muted">
                    {m.status.replace("_", " ")}{" "}
                    {formatDateTime(m.sent_at ?? m.created_at)}
                    {m.intent ? ` · ${m.intent.replace(/_/g, " ")}` : ""}
                  </span>
                </div>
                <pre className="live-email-body">{m.body}</pre>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3>Timeline</h3>
      <ol className="live-timeline">
        {timeline.map((e) => (
          <li key={e.id}>
            <span className="muted small">{formatDateTime(e.created_at)}</span>{" "}
            <strong>{e.actor}</strong> {e.summary}
          </li>
        ))}
      </ol>
      <p className="small">
        <Link to={`/customers/${ctx.customer_id}`}>
          Open the customer record →
        </Link>
      </p>
    </div>
  );
}
