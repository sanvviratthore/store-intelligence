"""
dashboard/live.py — Live terminal dashboard polling the Store Intelligence API.
Updates every 3 seconds. Run: python -m dashboard.live
"""

import time
import requests
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.columns import Columns
from rich import box

API_BASE = "http://localhost:8000"
STORE_ID = "ST1008"
REFRESH_INTERVAL = 3  # seconds

console = Console()


def fetch(path: str) -> dict:
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def render_metrics(data: dict) -> Panel:
    if "error" in data:
        return Panel(f"[red]Error: {data['error']}[/red]", title="📊 Metrics")

    lines = []
    lines.append(f"[bold cyan]Unique Visitors:[/bold cyan]   {data.get('unique_visitors', 0)}")
    cr = data.get('conversion_rate', 0)
    cr_color = "green" if cr >= 0.2 else "yellow" if cr >= 0.1 else "red"
    lines.append(f"[bold cyan]Conversion Rate:[/bold cyan]   [{cr_color}]{cr:.1%}[/{cr_color}]")
    lines.append(f"[bold cyan]Queue Depth:[/bold cyan]       {data.get('current_queue_depth', 0)}")
    ar = data.get('abandonment_rate', 0)
    lines.append(f"[bold cyan]Abandonment Rate:[/bold cyan]  {ar:.1%}")
    lines.append(f"[dim]As of: {data.get('as_of', '')[:19]}[/dim]")

    return Panel("\n".join(lines), title="📊 Live Metrics", border_style="cyan")


def render_funnel(data: dict) -> Panel:
    if "error" in data or "funnel" not in data:
        return Panel("[red]No funnel data[/red]", title="🔻 Funnel")

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta")
    table.add_column("Stage", style="white")
    table.add_column("Visitors", justify="right", style="cyan")
    table.add_column("Drop-off", justify="right")

    for stage in data["funnel"]:
        drop = stage["drop_off_pct"]
        drop_color = "red" if drop > 50 else "yellow" if drop > 20 else "green"
        drop_str = f"[{drop_color}]{drop:.1f}%[/{drop_color}]" if drop > 0 else "[dim]—[/dim]"
        table.add_row(stage["stage"], str(stage["visitors"]), drop_str)

    return Panel(table, title="🔻 Conversion Funnel", border_style="magenta")


def render_heatmap(data: dict) -> Panel:
    if "error" in data or "zones" not in data:
        return Panel("[red]No heatmap data[/red]", title="🗺 Heatmap")

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold yellow")
    table.add_column("Zone", style="white")
    table.add_column("Score", justify="right")
    table.add_column("Visits", justify="right", style="cyan")
    table.add_column("Avg Dwell", justify="right", style="dim")

    for zone in data["zones"][:6]:  # top 6
        score = zone["normalised_score"]
        bar = "█" * (score // 10) + "░" * (10 - score // 10)
        score_color = "green" if score > 60 else "yellow" if score > 30 else "red"
        dwell_sec = zone["avg_dwell_ms"] / 1000
        table.add_row(
            zone["zone_id"],
            f"[{score_color}]{bar}[/{score_color}] {score}",
            str(zone["visit_count"]),
            f"{dwell_sec:.0f}s"
        )

    conf = data.get("data_confidence", "?")
    conf_color = "green" if conf == "HIGH" else "yellow"
    footer = f"[{conf_color}]Data confidence: {conf}[/{conf_color}]"

    return Panel(
        table.__str__() + "\n" + footer if False else table,
        title="🗺 Zone Heatmap",
        border_style="yellow",
        subtitle=f"[{conf_color}]confidence: {conf}[/{conf_color}]"
    )


def render_anomalies(data: dict) -> Panel:
    if "error" in data:
        return Panel("[red]Error fetching anomalies[/red]", title="⚠ Anomalies")

    anomalies = data.get("active_anomalies", [])
    if not anomalies:
        return Panel("[green]✓ No active anomalies[/green]", title="⚠ Anomalies", border_style="green")

    lines = []
    for a in anomalies:
        sev = a["severity"]
        color = "red" if sev == "CRITICAL" else "yellow" if sev == "WARN" else "blue"
        lines.append(f"[{color}][{sev}][/{color}] {a['anomaly_id']}")
        lines.append(f"  [dim]{a['description']}[/dim]")
        lines.append(f"  [italic cyan]→ {a['suggested_action']}[/italic cyan]")

    return Panel("\n".join(lines), title=f"⚠ Anomalies ({len(anomalies)})", border_style="red")


def render_health(data: dict) -> Panel:
    if "error" in data:
        return Panel("[red]API unreachable[/red]", title="💚 Health")

    status = data.get("status", "unknown")
    color = "green" if status == "healthy" else "red"
    total = data.get("total_events_ingested", 0)
    lines = [
        f"[{color}]Status: {status.upper()}[/{color}]",
        f"[cyan]Total events:[/cyan] {total}",
    ]
    for s in data.get("stores", []):
        feed_color = "red" if s["status"] == "STALE_FEED" else "green"
        lines.append(f"[{feed_color}]{s['store_id']}: {s['status']} (lag {s['lag_seconds']:.0f}s)[/{feed_color}]")

    return Panel("\n".join(lines), title="💚 Health", border_style=color)


def build_dashboard() -> Layout:
    metrics = fetch(f"/stores/{STORE_ID}/metrics")
    funnel  = fetch(f"/stores/{STORE_ID}/funnel")
    heatmap = fetch(f"/stores/{STORE_ID}/heatmap")
    anomalies = fetch(f"/stores/{STORE_ID}/anomalies")
    health  = fetch("/health")

    now = datetime.now().strftime("%H:%M:%S")

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="top", size=12),
        Layout(name="bottom", size=14),
        Layout(name="footer", size=3),
    )

    layout["header"].update(Panel(
        f"[bold white]🏪 Purplle Store Intelligence — ST1008[/bold white]   [dim]Last refresh: {now}[/dim]",
        style="on dark_blue"
    ))

    layout["top"].split_row(
        Layout(render_metrics(metrics), name="metrics"),
        Layout(render_funnel(funnel), name="funnel"),
        Layout(render_health(health), name="health"),
    )

    layout["bottom"].split_row(
        Layout(render_heatmap(heatmap), name="heatmap"),
        Layout(render_anomalies(anomalies), name="anomalies"),
    )

    layout["footer"].update(Panel(
        f"[dim]Refreshing every {REFRESH_INTERVAL}s · Ctrl+C to exit · API: {API_BASE}[/dim]"
    ))

    return layout


def main():
    console.print("[bold cyan]Starting Purplle Store Intelligence Dashboard...[/bold cyan]")
    console.print(f"[dim]Connecting to {API_BASE}[/dim]\n")

    with Live(build_dashboard(), refresh_per_second=1, screen=True) as live:
        while True:
            time.sleep(REFRESH_INTERVAL)
            live.update(build_dashboard())


if __name__ == "__main__":
    main()
