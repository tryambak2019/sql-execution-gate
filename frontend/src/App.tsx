import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { VisualizationSpec } from "vega-embed";

type Role = "user" | "assistant";
type ApprovalStatus = "pending" | "approved" | "rejected";
type RequestStage = "idle" | "starting" | "working" | "streaming";
type OutputMode = "analysis" | "visualize";

interface Message {
  id: string;
  role: Role;
  content: string;
  agent?: string;
  approvalStatus?: ApprovalStatus;
  visualizeAfterApproval?: boolean;
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
const USER_ID = "sql-execution-gate-demo-user";
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

interface SqlReview {
  status: "ready_for_approval";
  sql_fingerprint: string;
  referenced_tables: string[];
  estimated_bytes: number;
  maximum_bytes_billed: number;
}

function makeId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1000)), units.length - 1);
  return `${(bytes / 1000 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
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

function suggestFollowUpQuestions(context: string): string[] {
  const normalized = context.toLowerCase();

  if (/revenue|sales|price|spend/.test(normalized)) {
    return [
      "Break this revenue down by product category.",
      "How has this revenue changed month over month?",
      "Which countries contributed the most to this revenue?",
    ];
  }
  if (/cancel|complete|status|order/.test(normalized)) {
    return [
      "Show the monthly trend for these order statuses.",
      "Which product categories have the highest cancellation rate?",
      "Compare average order value across order statuses.",
    ];
  }
  if (/product|category|brand|item/.test(normalized)) {
    return [
      "Compare these products by units sold and revenue.",
      "How has demand for the leading categories changed over time?",
      "Which customer countries buy these products most often?",
    ];
  }
  if (/user|customer|country|traffic|source/.test(normalized)) {
    return [
      "Compare customer count and revenue by country.",
      "Which traffic sources produce the highest average order value?",
      "How has customer acquisition changed month over month?",
    ];
  }
  return [
    "Show how this result changes month over month.",
    "Break this result down by product category.",
    "Compare this result across customer countries.",
  ];
}

function explicitlyRequestsVisualization(text: string): boolean {
  const normalized = text.toLowerCase().replace(/\s+/g, " ").trim();
  if (
    /\b(?:do not|don't|dont|without|no need to|avoid|rather than)\b.{0,50}\b(?:plot|chart|graph|visuali[sz])/.test(
      normalized,
    ) ||
    /\bnot\b.{0,25}\b(?:plot|chart|graph|visuali[sz])/.test(
      normalized,
    )
  ) {
    return false;
  }

  return (
    /(?:^|\b(?:please|and|then|also|can you|could you|would you)\s+)(?:plot|chart|graph|visuali[sz]e)\b/.test(
      normalized,
    ) ||
    /\b(?:show|display|render|create|generate|draw|make)\b.{0,40}\b(?:plot|chart|graph|visuali[sz]ation)\b/.test(
      normalized,
    ) ||
    /\bas\s+(?:a\s+)?(?:plot|chart|graph|visuali[sz]ation)\b/.test(normalized)
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
  onDecision: (
    messageId: string,
    decision: "yes" | "no",
    visualizeAfterApproval: boolean,
  ) => void;
}) {
  const sql = message.content.match(SQL_BLOCK)?.[1]?.trim();
  const [review, setReview] = useState<SqlReview | null>(null);
  const [reviewState, setReviewState] = useState<
    "loading" | "ready" | "blocked"
  >("loading");
  const [reviewError, setReviewError] = useState("");

  useEffect(() => {
    if (!sql || message.approvalStatus !== "pending") return;
    const controller = new AbortController();
    setReview(null);
    setReviewState("loading");
    setReviewError("");
    fetch("/sql/preflight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const problem = (await response.json().catch(() => ({}))) as {
            detail?: string;
          };
          throw new Error(problem.detail ?? "Query preflight failed.");
        }
        return (await response.json()) as SqlReview;
      })
      .then((payload) => {
        setReview(payload);
        setReviewState("ready");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setReviewError(
          error instanceof Error ? error.message : "Query preflight failed.",
        );
        setReviewState("blocked");
      });
    return () => controller.abort();
  }, [message.approvalStatus, sql]);

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
      <div className={`preflight-panel ${reviewState}`} aria-live="polite">
        {reviewState === "loading" && <strong>Running BigQuery dry run...</strong>}
        {reviewState === "blocked" && (
          <>
            <strong>Approval blocked</strong>
            <span>{reviewError}</span>
          </>
        )}
        {review && (
          <>
            <div>
              <span>Estimated scan</span>
              <strong>{formatBytes(review.estimated_bytes)}</strong>
            </div>
            <div>
              <span>Enforced ceiling</span>
              <strong>{formatBytes(review.maximum_bytes_billed)}</strong>
            </div>
            <div className="referenced-tables">
              <span>Referenced tables</span>
              {review.referenced_tables.length > 0 ? (
                review.referenced_tables.map((table) => <code key={table}>{table}</code>)
              ) : (
                <code>No tables referenced</code>
              )}
            </div>
          </>
        )}
      </div>
      <SchemaPanel sql={sql} />
      <p>
        SQL Execution Gate will not submit a BigQuery query job until the dry
        run passes and you explicitly approve this exact SQL.
      </p>
      <div className="approval-actions">
        <button
          className="secondary-button"
          disabled={disabled}
          onClick={() => onDecision(message.id, "no", false)}
          type="button"
        >
          Reject
        </button>
        <button
          className="primary-button"
          disabled={disabled || reviewState !== "ready"}
          onClick={() =>
            onDecision(message.id, "yes", Boolean(message.visualizeAfterApproval))
          }
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
  const [outputMode, setOutputMode] = useState<OutputMode>("analysis");
  const [modeManuallySelected, setModeManuallySelected] = useState(false);
  const [backendState, setBackendState] = useState<
    "checking" | "ready" | "unavailable"
  >("checking");
  const transcriptRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const pendingApproval = useMemo(
    () => messages.some((message) => message.approvalStatus === "pending"),
    [messages],
  );

  const followUpQuestions = useMemo(() => {
    if (isLoading || pendingApproval) return [];

    const latestMessage = messages.at(-1);
    const latestApproval = [...messages].reverse().find(
      (message) => message.approvalStatus !== undefined,
    );
    if (
      latestApproval?.approvalStatus !== "approved" ||
      latestMessage?.role !== "assistant" ||
      !latestMessage.content ||
      SQL_BLOCK.test(latestMessage.content) ||
      /could not be completed|request failed|could not be created/i.test(
        latestMessage.content,
      )
    ) {
      return [];
    }

    const latestBusinessQuestion = [...messages].reverse().find(
      (message) =>
        message.role === "user" &&
        !/^(yes|no)$/i.test(message.content.trim()),
    );
    return suggestFollowUpQuestions(
      `${latestBusinessQuestion?.content ?? ""} ${latestMessage.content}`,
    );
  }, [isLoading, messages, pendingApproval]);

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

  async function sendMessage(
    text: string,
    visualizeAfterApproval = false,
  ): Promise<{ content: string; succeeded: boolean }> {
    const cleanText = text.trim();
    if (!cleanText || isLoading) return { content: "", succeeded: false };

    setInput("");
    setIsLoading(true);
    setRequestStage("starting");
    const userMessage: Message = {
      id: makeId("user"),
      role: "user",
      content: cleanText,
    };
    const assistantId = makeId("assistant");
    let assistantContent = "";
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
        if (response.status === 422) {
          const payload = (await response.json()) as { detail?: string };
          assistantContent =
            payload.detail ?? "The request violates the SQL execution policy.";
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content: assistantContent,
                    agent: "sql_execution_gate",
                  }
                : message,
            ),
          );
          return { content: assistantContent, succeeded: true };
        }
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
            assistantContent = content;
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
              visualizeAfterApproval:
                SQL_BLOCK.test(content) && visualizeAfterApproval
                  ? true
                  : message.visualizeAfterApproval,
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
      const safeContent = assistantContent.replace(VEGA_LITE_BLOCK, "").trim();
      assistantContent = safeContent;
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? { ...message, content: safeContent, images: [] }
            : message,
        ),
      );
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
      return { content: detail, succeeded: false };
    } finally {
      setIsLoading(false);
      setRequestStage("idle");
    }
    return { content: assistantContent, succeeded: Boolean(assistantContent) };
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
    const visualizeAfterApproval = outputMode === "visualize";
    setOutputMode("analysis");
    setModeManuallySelected(false);
    void sendMessage(input, visualizeAfterApproval);
  }

  function handleApproval(
    messageId: string,
    decision: "yes" | "no",
    visualizeAfterApproval: boolean,
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
    void (async () => {
      const outcome = await sendMessage(decision);
      if (
        decision === "yes" &&
        visualizeAfterApproval &&
        outcome.succeeded &&
        /\b(result|rows?|total|average|count|sales|revenue|data)\b/i.test(
          outcome.content,
        )
      ) {
        await plotLatestResult();
      }
    })();
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/app/" aria-label="SQL Execution Gate home">
          <span className="brand-mark">SQL</span>
          <span>
            <strong>SQL Execution Gate</strong>
            <small>Human approval before agent-generated SQL runs</small>
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
          <a
            className="github-link"
            href="https://github.com/tryambak2019/sql-execution-gate"
            target="_blank"
            rel="noreferrer"
            aria-label="View SQL Execution Gate on GitHub"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 2C6.48 2 2 6.58 2 12.23c0 4.52 2.87 8.35 6.84 9.71.5.09.68-.22.68-.49v-1.92c-2.78.62-3.37-1.21-3.37-1.21-.45-1.18-1.11-1.49-1.11-1.49-.91-.64.07-.63.07-.63 1 .07 1.53 1.06 1.53 1.06.89 1.57 2.34 1.12 2.91.86.09-.67.35-1.12.63-1.38-2.22-.26-4.56-1.14-4.56-5.06 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.3.1-2.71 0 0 .84-.28 2.75 1.05A9.32 9.32 0 0 1 12 6.92a9.3 9.3 0 0 1 2.5.35c1.91-1.33 2.75-1.05 2.75-1.05.55 1.41.2 2.45.1 2.71.64.72 1.03 1.63 1.03 2.75 0 3.93-2.34 4.8-4.57 5.05.36.32.68.94.68 1.9v2.82c0 .27.18.59.69.49A10.25 10.25 0 0 0 22 12.23C22 6.58 17.52 2 12 2Z" />
            </svg>
            <span>GitHub</span>
          </a>
        </div>
      </header>

      <main className="workspace">
        <section className="hero">
          <span className="eyebrow">Plan → review → execute</span>
          <h1>SQL Execution Gate</h1>
          <p>
            Turn a business question into schema-grounded SQL, inspect its
            tables and estimated cost, then approve or reject execution.
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
                    {
                      question: "Which 10 products generated the most revenue?",
                      label: "Join + aggregation",
                    },
                    {
                      question: "Show monthly revenue and order count over the dataset’s latest 12 months.",
                      label: "Time-series analysis",
                    },
                    {
                      question: "Compare completed and cancelled orders by product category.",
                      label: "Multi-table comparison",
                    },
                  ].map((example) => (
                    <button
                      disabled={backendState !== "ready"}
                      key={example.question}
                      onClick={() => void sendMessage(example.question)}
                      type="button"
                    >
                      <span className="example-copy">
                        <strong>{example.question}</strong>
                        <small>{example.label}</small>
                      </span>
                      <span aria-hidden="true">↗</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
              {messages.map((message) => {
                const charts = extractVegaLiteSpecs(message.content);
                return (
                  <article
                    className={`message ${message.role}`}
                    key={message.id}
                  >
                  <div className="message-meta">
                    {message.role === "user"
                      ? "You"
                      : message.agent?.replace(/_/g, " ") || "SQL Execution Gate"}
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
              })}
              {followUpQuestions.length > 0 && (
                <section className="follow-up-panel" aria-label="Suggested next questions">
                  <span className="eyebrow">Continue exploring</span>
                  <h3>What would you like to investigate next?</h3>
                  <div className="follow-up-list">
                    {followUpQuestions.map((question) => (
                      <button
                        disabled={backendState !== "ready"}
                        key={question}
                        onClick={() => void sendMessage(question)}
                        type="button"
                      >
                        <span>{question}</span>
                        <span aria-hidden="true">↗</span>
                      </button>
                    ))}
                  </div>
                  <button
                    className="ask-another-button"
                    onClick={() => composerRef.current?.focus()}
                    type="button"
                  >
                    Ask another question
                  </button>
                </section>
              )}
              </>
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
            <div className="output-mode" aria-label="Response mode" role="group">
              <button
                aria-pressed={outputMode === "analysis"}
                disabled={isLoading || Boolean(pendingApproval)}
                onClick={() => {
                  setOutputMode("analysis");
                  setModeManuallySelected(true);
                }}
                type="button"
              >
                Analyze
              </button>
              <button
                aria-pressed={outputMode === "visualize"}
                disabled={isLoading || Boolean(pendingApproval)}
                onClick={() => {
                  setOutputMode("visualize");
                  setModeManuallySelected(true);
                }}
                type="button"
              >
                Analyze + chart
              </button>
            </div>
            <textarea
              aria-label="Ask a data question"
              disabled={
                isLoading ||
                pendingApproval ||
                backendState !== "ready"
              }
              onChange={(event) => {
                const nextInput = event.target.value;
                setInput(nextInput);
                if (!modeManuallySelected) {
                  setOutputMode(
                    explicitlyRequestsVisualization(nextInput)
                      ? "visualize"
                      : "analysis",
                  );
                }
              }}
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
              ref={composerRef}
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
