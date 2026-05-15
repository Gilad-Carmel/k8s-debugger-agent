# Quickstart: Multi-Agent Workflow GUI

## Prerequisites

- kind cluster running with the `demo` namespace deployed (`make demo-deploy`)
- Agent stack running (`make dev`)
- Node.js 20+ installed

---

## One-command start (development)

```bash
make gui-dev
```

This runs the Vite dev server (port 5173) and the FastAPI agent (port 8000)
concurrently. Open http://localhost:5173 in your browser.

---

## Step-by-step

### 1. Install frontend dependencies (first time only)

```bash
make gui-install
# equivalent: cd gui && npm install
```

### 2. Deploy the demo workload

```bash
make demo-deploy
```

You should see the podinfo pod appear in the **Pod Status** panel as `Running`.

### 3. Trigger a failure scenario

Click one of the four scenario buttons in the GUI:

| Button | What it does |
|--------|-------------|
| **Crash Loop** | Calls podinfo `/panic` → CrashLoopBackOff |
| **Bad Deploy** | Applies v2 image with `RUNTIME_ERROR=true` |
| **OOM Kill** | Calls `/stress?mem=50` (pod has 32Mi limit) |
| **High Load** | Runs bombardier in-cluster → error rate spike |

The button becomes disabled while the scenario is running. The failing pod
displays a red badge in the **Pod Status** panel.

### 4. Watch the workflow

The **Workflow Diagram** lights up each node as the multi-agent pipeline runs:
`ingest` → `router` → expert → `reporter`.

The **Event Log** panel on the right shows raw SSE events with timestamps.

### 5. Approve or Reject

When `reporter` finishes, the **HITL Gate** node pulses amber and a modal
appears with the agent's root-cause summary and proposed fix.

- Click **Approve** to let the Solver execute the remediation.
- Click **Reject** to close the incident without remediation.

The workflow diagram continues after your decision, showing `solver` running
(if approved) and the final outcome.

### 6. Reset

```bash
make demo-reset
```

Tears down and re-creates the `demo` namespace with a fresh podinfo deployment.

---

## Production build

```bash
make gui-build   # vite build → gui/dist/
make gui-serve   # serves GUI + API from port 8000
```

Open http://localhost:8000.
