import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { VisualizationSpec } from "vega-embed";

type Role = "user" | "assistant";
type ApprovalStatus = "pending" | "approved" | "rejected";
type RequestStage = "idle" | "starting" | "working" | "streaming";

interface Message {
  id: string;
  role: Role;
  content: string;
  agent?: string;
  approvalStatus?: ApprovalStatus;
  images?: MessageImage[];
}

interface Session {
  appName: string;
  userId: string;
  sessionId: string;
}

interface VisualizationResponse {
  insight?: string;
  spec: VisualizationSpec;
}

interface AdkPart {
  text?: string;
  inlineData?: InlineData;
  inline_data?: InlineData;
}

interface AdkEvent {
  author?: string;
  content?: {
    parts?: AdkPart[];
  };
  actions?: {
    stateDelta?: {
      final_report_with_citations?: string;
    };
    artifactDelta?: Record<string, number>;
    artifact_delta?: Record<string, number>;
  };
}

const APP_NAME = "app";
const USER_ID = "fsbq-demo-user";
const SQL_BLOCK = /```sql\s*([\s\S]*?)```/i;
const VEGA_LITE_BLOCK = /```vega-lite\s*([\s\S]*?)```/gi;

interface InlineData {
  data?: string;
  mimeType?: string;
  mime_type?: string;
}

interface MessageImage {
  dataUrl: string;
  mimeType: string;
}

interface TableSchema {
  table: string;
  columns: Array<{
    name: string;
    type: string;
  }>;
}

function makeId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

function mergeEventText(current: string, incoming: string): string {
  if (!current) return incoming;
  if (current === incoming || current.endsWith(incoming)) return current;
  if (incoming.startsWith(current)) return incoming;
  return `${current}${incoming}`;
}

function messageContentForDisplay(message: Message): string {
  const withoutCharts = message.content.replace(VEGA_LITE_BLOCK, "").trim();
  if (message.approvalStatus !== "pending") return withoutCharts;
  return withoutCharts.replace(SQL_BLOCK, "").trim();
}

function extractVegaLiteSpecs(content: string): VisualizationSpec[] {
  return [...content.matchAll(VEGA_LITE_BLOCK)].flatMap((match) => {
    try {
      return [JSON.parse(match[1]) as VisualizationSpec];
    } catch {
      return [];
    }
  });
}

function VegaLiteChart({ spec }: { spec: VisualizationSpec }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!chartRef.current) return;
    let disposed = false;
    let view: { finalize: () => void } | undefined;
    setError(false);
    void import("vega-embed")
      .then(({ default: embed }) =>
        embed(chartRef.current!, spec, {
          actions: {
            export: true,
            source: false,
            compiled: false,
            editor: false,
          },
          renderer: "svg",
        }),
      )
      .then((result) => {
        if (disposed) result.view.finalize();
        else view = result.view;
      })
      .catch(() => {
        if (!disposed) setError(true);
      });
    return () => {
      disposed = true;
      view?.finalize();
    };
  }, [spec]);

  if (error) {
    return <p className="chart-error">This visualization could not be rendered.</p>;
  }
  return <div aria-label="Interactive analytics visualization" ref={chartRef} />;
}

function selectedTableFromSql(sql: string): string | null {
  return sql.match(/`([^`]+)`/)?.[1] ?? null;
}

function SchemaPanel({ sql }: { sql: string }) {
  const table = selectedTableFromSql(sql);
  const [schema, setSchema] = useState<TableSchema | null>(null);
  const [schemaState, setSchemaState] = useState<
    "loading" | "ready" | "unavailable"
  >("loading");

  useEffect(() => {
    if (!table) {
      setSchemaState("unavailable");
      return;
    }

    const controller = new AbortController();
    setSchema(null);
    setSchemaState("loading");
    fetch(`/schema?table=${encodeURIComponent(table)}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Schema request failed (${response.status})`);
        return (await response.json()) as TableSchema;
      })
      .then((payload) => {
        setSchema(payload);
        setSchemaState("ready");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setSchemaState("unavailable");
      });

    return () => controller.abort();
  }, [table]);

  return (
    <details className="schema-panel">
      <summary>
        <span>
          <strong>Selected table schema</strong>
          <small>{table ?? "No table detected"}</small>
        </span>
        <span className={`schema-status ${schemaState}`}>
          {schemaState === "loading"
            ? "Loading"
            : schemaState === "ready"
              ? "Live"
              : "Unavailable"}
        </span>
      </summary>
      {schema && (
        <div className="schema-columns">
          {schema.columns.map((column) => (
            <div key={column.name}>
              <code>{column.name}</code>
              <span>{column.type}</span>
            </div>
          ))}
        </div>
      )}
      {schemaState === "unavailable" && (
        <p>Schema could not be loaded. SQL review and approval remain available.</p>
      )}
    </details>
  );
}

function extractEventText(event: AdkEvent): string {
  const finalReport = event.actions?.stateDelta?.final_report_with_citations;
  if (finalReport) {
    return finalReport;
  }

  return (
    event.content?.parts
      ?.map((part) => part.text ?? "")
      .filter(Boolean)
      .join("") ?? ""
  );
}

function toBase64DataUrl(mimeType: string, data: string): string {
  const normalized = data
    .replace(/\s/g, "")
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const padding = (4 - (normalized.length % 4)) % 4;
  return `data:${mimeType};base64,${normalized}${"=".repeat(padding)}`;
}

function extractEventImages(event: AdkEvent): MessageImage[] {
  return (
    event.content?.parts
      ?.map((part) => part.inlineData ?? part.inline_data)
      .filter((inlineData): inlineData is InlineData => Boolean(inlineData?.data))
      .map((inlineData) => {
        const mimeType =
          inlineData.mimeType ?? inlineData.mime_type ?? "image/png";
        return {
          mimeType,
          dataUrl: toBase64DataUrl(mimeType, inlineData.data!),
        };
      })
      .filter((image) => image.mimeType.startsWith("image/")) ?? []
  );
}

function extractArtifactRefs(
  event: AdkEvent,
): Array<{ filename: string; version: number }> {
  const artifactDelta =
    event.actions?.artifactDelta ?? event.actions?.artifact_delta ?? {};
  return Object.entries(artifactDelta).map(([filename, version]) => ({
    filename,
    version,
  }));
}

function canPlotMessage(message: Message): boolean {
  return (
    message.role === "assistant" &&
    Boolean(message.content) &&
    !message.images?.length &&
    extractVegaLiteSpecs(message.content).length === 0 &&
    !SQL_BLOCK.test(message.content) &&
    /\b(result|rows?|total|average|count|sales|data)\b/i.test(message.content)
  );
}

async function loadArtifactImage(
  activeSession: Session,
  filename: string,
  version: number,
): Promise<MessageImage | null> {
  const artifactUrl =
    `/api/apps/${encodeURIComponent(activeSession.appName)}` +
    `/users/${encodeURIComponent(activeSession.userId)}` +
    `/sessions/${encodeURIComponent(activeSession.sessionId)}` +
    `/artifacts/${encodeURIComponent(filename)}` +
    `/versions/${version}`;
  const response = await fetch(artifactUrl);
  if (!response.ok) return null;

  const responseType = response.headers.get("content-type") ?? "";
  if (responseType.startsWith("image/")) {
    return {
      mimeType: responseType.split(";", 1)[0],
      // Keep the image on its same-origin artifact URL. In particular, this
      // avoids Safari treating a fetched blob URL as a broken image even
      // though the artifact request itself succeeded.
      dataUrl: artifactUrl,
    };
  }

  const payload = (await response.json()) as {
    inlineData?: InlineData;
    inline_data?: InlineData;
  };
  const inlineData = payload.inlineData ?? payload.inline_data;
  if (!inlineData?.data) return null;

  const mimeType = inlineData.mimeType ?? inlineData.mime_type ?? "";
  if (!mimeType.startsWith("image/")) return null;
  return {
    mimeType,
    dataUrl: toBase64DataUrl(mimeType, inlineData.data),
  };
}

async function readSse(
  response: Response,
  onEvent: (event: AdkEvent) => void,
): Promise<void> {
  if (!response.body) {
    throw new Error("The agent returned an empty response.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length === 0) return;
    const payload = dataLines.join("\n");
    dataLines = [];
    try {
      onEvent(JSON.parse(payload) as AdkEvent);
    } catch {
      // Ignore non-JSON keepalive events without exposing raw agent data.
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });

    const lines = buffer.split(/\r?\n/);
    buffer = done ? "" : (lines.pop() ?? "");

    for (const line of lines) {
      if (line === "") {
        dispatch();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }

    if (done) {
      if (buffer.startsWith("data:")) {
        dataLines.push(buffer.slice(5).trimStart());
      }
      dispatch();
      break;
    }
  }
}

function ApprovalCard({
  message,
  disabled,
  onDecision,
}: {
  message: Message;
  disabled: boolean;
  onDecision: (messageId: string, decision: "yes" | "no") => void;
}) {
  const sql = message.content.match(SQL_BLOCK)?.[1]?.trim();
  if (!sql || !message.approvalStatus) return null;

  if (message.approvalStatus !== "pending") {
    return (
      <p className={`approval-outcome ${message.approvalStatus}`}>
        {message.approvalStatus === "approved"
          ? "Approved for read-only execution"
          : "Rejected — query was not executed"}
      </p>
    );
  }

  return (
    <section className="approval-card" aria-label="SQL approval required">
      <div className="approval-heading">
        <div>
          <span className="eyebrow">Human approval required</span>
          <h3>Review generated SQL</h3>
        </div>
        <span className="gate-badge">HITL gate</span>
      </div>
      <pre>
        <code>{sql}</code>
      </pre>
      <SchemaPanel sql={sql} />
      <p>
        FSBQ will not ask BigQuery to execute this query until you explicitly
        approve it.
      </p>
      <div className="approval-actions">
        <button
          className="secondary-button"
          disabled={disabled}
          onClick={() => onDecision(message.id, "no")}
          type="button"
        >
          Reject
        </button>
        <button
          className="primary-button"
          disabled={disabled}
          onClick={() => onDecision(message.id, "yes")}
          type="button"
        >
          Approve &amp; execute
        </button>
      </div>
    </section>
  );
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [requestStage, setRequestStage] = useState<RequestStage>("idle");
  const [backendState, setBackendState] = useState<
    "checking" | "ready" | "unavailable"
  >("checking");
  const transcriptRef = useRef<HTMLDivElement>(null);

  const pendingApproval = useMemo(
    () => messages.some((message) => message.approvalStatus === "pending"),
    [messages],
  );

  useEffect(() => {
    fetch("/api/docs", { method: "GET" })
      .then((response) =>
        setBackendState(response.ok ? "ready" : "unavailable"),
      )
      .catch(() => setBackendState("unavailable"));
  }, []);

  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  useEffect(() => {
    if (!isLoading) return;
    const startedAt = Date.now();
    setElapsedSeconds(0);
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [isLoading]);

  async function ensureSession(): Promise<Session> {
    if (session) return session;

    const sessionId = crypto.randomUUID();
    const response = await fetch(
      `/api/apps/${APP_NAME}/users/${USER_ID}/sessions/${sessionId}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      },
    );

    if (!response.ok) {
      throw new Error(`Could not start a session (${response.status}).`);
    }

    const payload = (await response.json()) as {
      appName?: string;
      userId?: string;
      id?: string;
    };
    const created = {
      appName: payload.appName ?? APP_NAME,
      userId: payload.userId ?? USER_ID,
      sessionId: payload.id ?? sessionId,
    };
    setSession(created);
    return created;
  }

  async function sendMessage(text: string): Promise<void> {
    const cleanText = text.trim();
    if (!cleanText || isLoading) return;

    setInput("");
    setIsLoading(true);
    setRequestStage("starting");
    const userMessage: Message = {
      id: makeId("user"),
      role: "user",
      content: cleanText,
    };
    const assistantId = makeId("assistant");
    setMessages((current) => [
      ...current,
      userMessage,
      {
        id: assistantId,
        role: "assistant",
        content: "",
        images: [],
      },
    ]);

    try {
      const activeSession = await ensureSession();
      setRequestStage("working");
      const response = await fetch("/api/run_sse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          appName: activeSession.appName,
          userId: activeSession.userId,
          sessionId: activeSession.sessionId,
          newMessage: {
            role: "user",
            parts: [{ text: cleanText }],
          },
          streaming: true,
        }),
      });

      if (!response.ok) {
        throw new Error(`Agent request failed (${response.status}).`);
      }

      await readSse(response, (event) => {
        setRequestStage("streaming");
        const eventText = extractEventText(event);
        const eventImages = extractEventImages(event);
        const artifactRefs = extractArtifactRefs(event);
        if (
          !eventText &&
          eventImages.length === 0 &&
          artifactRefs.length === 0
        ) {
          return;
        }

        setMessages((current) =>
          current.map((message) => {
            if (message.id !== assistantId) return message;
            const content = mergeEventText(message.content, eventText);
            const images = [...(message.images ?? [])];
            for (const image of eventImages) {
              if (
                !images.some(
                  (currentImage) => currentImage.dataUrl === image.dataUrl,
                )
              ) {
                images.push(image);
              }
            }
            return {
              ...message,
              content,
              images,
              agent: event.author ?? message.agent,
              approvalStatus:
                SQL_BLOCK.test(content) &&
                /\b(yes|approve)\b/i.test(content) &&
                /\b(no|reject|cancel)\b/i.test(content)
                  ? "pending"
                  : message.approvalStatus,
            };
          }),
        );

        for (const artifact of artifactRefs) {
          void loadArtifactImage(
            activeSession,
            artifact.filename,
            artifact.version,
          )
            .then((image) => {
              if (!image) return;
              setMessages((current) =>
                current.map((message) => {
                  if (message.id !== assistantId) return message;
                  if (
                    message.images?.some(
                      (currentImage) => currentImage.dataUrl === image.dataUrl,
                    )
                  ) {
                    return message;
                  }
                  return {
                    ...message,
                    // The artifact is the durable, same-origin copy of an
                    // image that ADK may also emit inline. Replace that
                    // temporary inline copy rather than showing it twice.
                    images: [
                      ...(message.images ?? []).filter(
                        (currentImage) =>
                          !currentImage.dataUrl.startsWith("data:") ||
                          currentImage.mimeType !== image.mimeType,
                      ),
                      image,
                    ],
                  };
                }),
              );
            })
            .catch(() => {
              // The analytics text remains usable if an artifact expires or
              // cannot be loaded from the current session.
            });
        }
      });
    } catch (error) {
      const detail =
        error instanceof Error ? error.message : "Unexpected request failure.";
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: `The request could not be completed. ${detail}`,
              }
            : message,
        ),
      );
    } finally {
      setIsLoading(false);
      setRequestStage("idle");
    }
  }

  async function plotLatestResult(): Promise<void> {
    if (isLoading) return;
    setIsLoading(true);
    setRequestStage("working");
    const assistantId = makeId("assistant");
    setMessages((current) => [
      ...current,
      {
        id: assistantId,
        role: "assistant",
        content: "",
        images: [],
      },
    ]);
    try {
      const activeSession = await ensureSession();
      const response = await fetch("/visualize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          app_name: activeSession.appName,
          user_id: activeSession.userId,
          session_id: activeSession.sessionId,
        }),
      });
      if (!response.ok) {
        const problem = (await response.json().catch(() => ({}))) as {
          detail?: string;
        };
        throw new Error(problem.detail ?? `Chart request failed (${response.status}).`);
      }
      const chart = (await response.json()) as VisualizationResponse;
      const content = `${chart.insight ?? ""}\n\n\`\`\`vega-lite\n${JSON.stringify(chart.spec)}\n\`\`\``;
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? { ...message, content, agent: "analytics_agent" }
            : message,
        ),
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Unexpected chart failure.";
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? { ...message, content: `The chart could not be created. ${detail}` }
            : message,
        ),
      );
    } finally {
      setIsLoading(false);
      setRequestStage("idle");
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void sendMessage(input);
  }

  function handleApproval(
    messageId: string,
    decision: "yes" | "no",
  ): void {
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId
          ? {
              ...message,
              approvalStatus: decision === "yes" ? "approved" : "rejected",
            }
          : message,
      ),
    );
    void sendMessage(decision);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/app/" aria-label="FSBQ home">
          <span className="brand-mark">F</span>
          <span>
            <strong>FSBQ Agent</strong>
            <small>Safety-gated BigQuery assistant</small>
          </span>
        </a>
        <div className="status-group">
          <span className="status-pill">Read-only SQL</span>
          <span className={`connection ${backendState}`}>
            <i />
            {backendState === "checking"
              ? "Connecting"
              : backendState === "ready"
                ? "Agent ready"
                : "Backend unavailable"}
          </span>
        </div>
      </header>

      <main className="workspace">
        <section className="hero">
          <span className="eyebrow">Plan → review → execute</span>
          <h1>Ask your data. Approve the query.</h1>
          <p>
            FSBQ turns a business question into BigQuery SQL, shows the exact
            query for review, and waits for your decision before execution.
          </p>
          <div className="trust-row">
            <span>Live schema grounding</span>
            <span>Separate planner and executor</span>
            <span>Fail-closed recovery</span>
          </div>
        </section>

        <section className="chat-panel">
          <div className="transcript" ref={transcriptRef}>
            {messages.length === 0 ? (
              <div className="empty-state">
                <span className="eyebrow">Try a question</span>
                <h2>Explore TheLook ecommerce data</h2>
                <div className="example-grid">
                  {[
                    "Which 10 products generated the most revenue?",
                    "Show monthly revenue for the last 12 months in the data",
                    "Which customer countries have the highest order value?",
                  ].map((example) => (
                    <button
                      disabled={backendState !== "ready"}
                      key={example}
                      onClick={() => void sendMessage(example)}
                      type="button"
                    >
                      {example}
                      <span>↗</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((message) => {
                const charts = extractVegaLiteSpecs(message.content);
                return (
                  <article
                    className={`message ${message.role}`}
                    key={message.id}
                  >
                  <div className="message-meta">
                    {message.role === "user"
                      ? "You"
                      : message.agent?.replace(/_/g, " ") || "FSBQ"}
                  </div>
                  {message.content ? (
                    <div className="markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {messageContentForDisplay(message)}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <div
                      className="working-state"
                      aria-live="polite"
                      aria-label="Agent is working"
                    >
                      <div className="typing" aria-hidden="true">
                        <span />
                        <span />
                        <span />
                      </div>
                      <div>
                        <strong>
                          {requestStage === "starting"
                            ? "Starting secure session"
                            : requestStage === "streaming"
                              ? "Receiving response"
                              : "Agent is preparing a grounded response"}
                        </strong>
                        <small>
                          {elapsedSeconds}s elapsed
                          {elapsedSeconds >= 12
                            ? " · Complex requests can take up to 60 seconds"
                            : " · Reading live data context"}
                        </small>
                      </div>
                    </div>
                  )}
                  {message.role === "assistant" && (
                    <>
                      {charts.map((spec, index) => (
                        <figure
                          className="analytics-chart"
                          key={`${message.id}-chart-${index}`}
                        >
                          <VegaLiteChart spec={spec} />
                        </figure>
                      ))}
                      {message.images?.map((image, index) => (
                        <figure
                          className="analytics-artifact"
                          key={`${message.id}-${index}`}
                        >
                          <img
                            alt={`Analytics visualization ${index + 1}`}
                            src={image.dataUrl}
                          />
                        </figure>
                      ))}
                      {canPlotMessage(message) && (
                        <button
                          className="plot-result-chip"
                          disabled={isLoading || Boolean(pendingApproval)}
                          onClick={() => void plotLatestResult()}
                          type="button"
                        >
                          <span aria-hidden="true">▥</span>
                          Plot this result
                        </button>
                      )}
                      <ApprovalCard
                        disabled={isLoading}
                        message={message}
                        onDecision={handleApproval}
                      />
                    </>
                  )}
                  </article>
                );
              })
            )}
          </div>

          <div className="agent-disclaimer" role="note">
            <span aria-hidden="true">!</span>
            <p>
              AI agents can make mistakes. Review the generated SQL and verify
              results before relying on them.
            </p>
          </div>
          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              aria-label="Ask a data question"
              disabled={
                isLoading ||
                pendingApproval ||
                backendState !== "ready"
              }
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder={
                pendingApproval
                  ? "Approve or reject the SQL above to continue"
                  : "Ask a question about the dataset…"
              }
              rows={2}
              value={input}
            />
            <button
              className="send-button"
              disabled={
                !input.trim() ||
                isLoading ||
                pendingApproval ||
                backendState !== "ready"
              }
              type="submit"
            >
              {isLoading ? "Working…" : "Send"}
            </button>
          </form>
          <p className="composer-note">
            Enter to send · Shift + Enter for a new line
          </p>
        </section>
      </main>
    </div>
  );
}
