#!/usr/bin/env python3
"""
Export the compiled LangGraph workflow into visualization artifacts.

Outputs (by default under build/langgraph-viz/):
  - graph.json   : serialized nodes/edges
  - graph.mmd    : Mermaid diagram source
  - graph.html   : local HTML page that renders Mermaid
  - graph.txt    : optional ASCII graph (when supported)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Allow running this script directly from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agent.graph.builder import build_graph


def _as_str(value: Any) -> str:
    return str(value)


def _serialize_nodes(raw_nodes: Any) -> list[dict[str, str]]:
    if isinstance(raw_nodes, dict):
        return [{"id": _as_str(node_id)} for node_id in raw_nodes]
    if isinstance(raw_nodes, Iterable) and not isinstance(raw_nodes, (str, bytes)):
        result: list[dict[str, str]] = []
        for node in raw_nodes:
            node_id = getattr(node, "id", node)
            result.append({"id": _as_str(node_id)})
        return result
    return []


def _serialize_edges(raw_edges: Any) -> list[dict[str, str]]:
    if not isinstance(raw_edges, Iterable) or isinstance(raw_edges, (str, bytes)):
        return []

    result: list[dict[str, str]] = []
    for edge in raw_edges:
        source = getattr(edge, "source", None)
        target = getattr(edge, "target", None)

        if source is None and isinstance(edge, tuple) and len(edge) >= 2:
            source, target = edge[0], edge[1]

        if source is None or target is None:
            continue

        result.append({"source": _as_str(source), "target": _as_str(target)})

    return result


def _serialize_graph(runtime_graph: Any) -> dict[str, Any]:
    to_json = getattr(runtime_graph, "to_json", None)
    if callable(to_json):
        payload = to_json()
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"raw": payload}
        if isinstance(payload, dict):
            return payload

    nodes = _serialize_nodes(getattr(runtime_graph, "nodes", None))
    edges = _serialize_edges(getattr(runtime_graph, "edges", None))
    return {"nodes": nodes, "edges": edges}


def _build_html(mermaid_source: str) -> str:
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>LangGraph Visualization</title>
  <script type=\"module\">
    import mermaid from \"https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs\";
    mermaid.initialize({{ startOnLoad: true, theme: \"neutral\", securityLevel: \"loose\" }});
  </script>
  <style>
    :root {{
      --bg: #f5f2eb;
      --panel: #fffdf8;
      --fg: #1f2a2e;
      --accent: #d86227;
      --muted: #5f6d71;
      --ring: rgba(216, 98, 39, 0.25);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--fg);
      background:
        radial-gradient(1200px 600px at 0% -10%, #ffe5cc 0%, transparent 60%),
        radial-gradient(900px 500px at 100% 0%, #d8efe7 0%, transparent 55%),
        var(--bg);
      min-height: 100vh;
      padding: 2rem 1rem;
    }}
    .shell {{
      max-width: 1200px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid #eadfce;
      border-radius: 16px;
      box-shadow: 0 10px 24px rgba(0, 0, 0, 0.08);
      overflow: hidden;
    }}
    header {{
      padding: 1rem 1.25rem;
      border-bottom: 1px solid #f0e6d8;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    .title {{
      margin: 0;
      font-size: 1.05rem;
      letter-spacing: 0.02em;
    }}
    .hint {{
      margin: 0;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .accent {{ color: var(--accent); font-weight: 700; }}
    .canvas {{ padding: 1rem; overflow-x: auto; }}
    .mermaid {{ min-width: 720px; }}
    .mermaid svg {{ max-width: none; }}
    @media (max-width: 640px) {{
      body {{ padding: 1rem 0.5rem; }}
      .mermaid {{ min-width: 540px; }}
    }}
  </style>
</head>
<body>
  <main class=\"shell\">
    <header>
      <h1 class=\"title\">LangGraph Workflow <span class=\"accent\">Visualization</span></h1>
      <p class=\"hint\">Generated locally from the compiled graph</p>
    </header>
    <section class=\"canvas\">
      <div class=\"mermaid\">{mermaid_source}</div>
    </section>
  </main>
</body>
</html>
"""


def export_visualization(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    compiled_graph = build_graph()
    runtime_graph = compiled_graph.get_graph()

    graph_json = _serialize_graph(runtime_graph)
    graph_json_path = output_dir / "graph.json"
    graph_json_path.write_text(json.dumps(graph_json, indent=2), encoding="utf-8")

    draw_mermaid = getattr(runtime_graph, "draw_mermaid", None)
    mermaid_source = "graph TD\n  START --> END"
    if callable(draw_mermaid):
        mermaid_source = draw_mermaid()

    graph_mmd_path = output_dir / "graph.mmd"
    graph_mmd_path.write_text(mermaid_source, encoding="utf-8")

    draw_ascii = getattr(runtime_graph, "draw_ascii", None)
    if callable(draw_ascii):
      try:
        graph_txt_path = output_dir / "graph.txt"
        graph_txt_path.write_text(draw_ascii(), encoding="utf-8")
      except ImportError:
        # grandalf is optional; skip ASCII export when unavailable.
        pass

    graph_html_path = output_dir / "graph.html"
    graph_html_path.write_text(_build_html(mermaid_source), encoding="utf-8")

    print(f"Wrote: {graph_json_path}")
    print(f"Wrote: {graph_mmd_path}")
    print(f"Wrote: {graph_html_path}")
    if (output_dir / "graph.txt").exists():
        print(f"Wrote: {output_dir / 'graph.txt'}")
    print("Open graph.html in your browser to inspect the graph.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/langgraph-viz"),
        help="Directory where graph artifacts will be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export_visualization(args.output_dir)


if __name__ == "__main__":
    main()
