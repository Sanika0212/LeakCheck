"""Build every README figure, light and dark.

    uv run python scripts/make_figures.py

Inputs: ``runs/*/results.json`` and, for the animation, one real finding from the
warehouse (a held-out business, extra work found by the LLM extractor). Outputs go to
``docs/assets/<name>-{light,dark}.*``.
"""

from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import FancyBboxPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ASSETS = Path("docs/assets")
RUNS = Path("runs")
DPI = 200
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"
GOOD, WARN, BAD = "#0ca30c", "#fab219", "#d03b3b"
TYPES = [
    "uninvoiced_job",
    "unbilled_hours",
    "unbilled_materials",
    "unbilled_extra_work",
    "unapproved_discount",
]
TYPE_LABEL = {
    "uninvoiced_job": "never invoiced",
    "unbilled_hours": "hours not billed",
    "unbilled_materials": "materials not billed",
    "unbilled_extra_work": "extra work not billed",
    "unapproved_discount": "silent discount",
}


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    ink: str
    ink2: str
    muted: str
    grid: str
    axis: str
    card: str
    series: tuple[str, ...]


THEMES = {
    "light": Theme(
        "light",
        "#fcfcfb",
        "#0b0b0b",
        "#52514e",
        "#898781",
        "#e1e0d9",
        "#c3c2b7",
        "#f1f0eb",
        ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#8a5cd1"),
    ),
    "dark": Theme(
        "dark",
        "#1a1a19",
        "#ffffff",
        "#c3c2b7",
        "#898781",
        "#2c2c2a",
        "#383835",
        "#242422",
        ("#3987e5", "#d95926", "#199e70", "#c98500", "#9a73dd"),
    ),
}


def use_theme(mode: str) -> Theme:
    t = THEMES[mode]
    plt.rcParams.update(
        {
            "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 9.5,
            "figure.facecolor": t.surface,
            "axes.facecolor": t.surface,
            "savefig.facecolor": t.surface,
            "text.color": t.ink,
            "axes.labelcolor": t.ink2,
            "axes.edgecolor": t.axis,
            "xtick.color": t.muted,
            "ytick.color": t.muted,
            "xtick.labelcolor": t.ink2,
            "ytick.labelcolor": t.ink2,
            "grid.color": t.grid,
            "grid.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "legend.frameon": False,
            "legend.fontsize": 9,
        }
    )
    return t


def title(fig: Any, text: str, sub: str, t: Theme) -> None:
    fig.text(0.012, 0.975, text, ha="left", va="top", fontsize=12.5, weight="bold")
    fig.text(0.012, 0.905, sub, ha="left", va="top", fontsize=9, color=t.ink2)


def save(fig: Any, name: str, mode: str) -> None:
    path = ASSETS / f"{name}-{mode}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    print(path)


def load(name: str) -> Any:
    path = RUNS / name / "results.json"
    return json.loads(path.read_text()) if path.exists() else None


# ------------------------------------------------------------------------ banner


def banner(mode: str) -> str:
    t = THEMES[mode]
    w, h = 1200, 260
    rows = [
        ("Interior, 4 rooms", "$3,840", None),
        ("Exterior, 2 stories", "$9,120", None),
        ("Garage door, asked for by text", "$389", "never billed"),
        ("Cabinets, 28 doors", "$2,610", None),
    ]
    shapes = []
    x0, y0 = 690, 46
    shapes.append(f'<rect x="{x0}" y="{y0}" width="450" height="176" rx="12" fill="{t.card}"/>')
    for i, (label, amount, flag) in enumerate(rows):
        y = y0 + 22 + i * 38
        color = BAD if flag else t.ink2
        if flag:
            shapes.append(
                f'<rect x="{x0 + 10}" y="{y - 16}" width="430" height="30" rx="7" '
                f'fill="{BAD}" fill-opacity="0.12" stroke="{BAD}" stroke-width="1.5"/>'
            )
        shapes.append(
            f'<text x="{x0 + 24}" y="{y + 4}" font-family="{FONT}" font-size="16" '
            f'fill="{t.ink if not flag else BAD}">{label}</text>'
        )
        shapes.append(
            f'<text x="{x0 + 426}" y="{y + 4}" text-anchor="end" font-family="{FONT}" '
            f'font-size="16" font-weight="600" fill="{color}">{amount}</text>'
        )
        if flag:
            shapes.append(
                f'<text x="{x0 + 330}" y="{y + 4}" text-anchor="end" font-family="{FONT}" '
                f'font-size="13" font-weight="700" fill="{BAD}">{flag}</text>'
            )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="LeakCheck: finds the work small contractors did but never billed">
  <rect width="{w}" height="{h}" rx="18" fill="{t.surface}"/>
  <text x="56" y="112" font-family="{FONT}" font-size="62" font-weight="700" fill="{t.ink}">LeakCheck</text>
  <text x="58" y="158" font-family="{FONT}" font-size="22" fill="{t.ink2}">Finds the work small contractors did</text>
  <text x="58" y="188" font-family="{FONT}" font-size="22" fill="{t.ink2}">but never billed, and drafts the invoice.</text>
  {"".join(shapes)}
</svg>
"""


# --------------------------------------------------------------------- animation


def story(warehouse: Path = Path("data/warehouse.duckdb")) -> dict[str, Any]:
    """One real extra-work finding on a held-out business, with its messages and notes."""
    import duckdb

    businesses = [
        json.loads(x) for x in Path("data/generated/businesses.jsonl").read_text().splitlines()
    ]
    test = [b["business_id"] for b in businesses if b["split"] == "test"]
    with duckdb.connect(str(warehouse), read_only=True) as con:
        row = con.execute(
            "select f.job_id, f.amount, f.evidence, j.quote_total, j.job_type from gold.findings f "
            "join gold.job_financials j using (job_id) where f.finding_type = 'unbilled_extra_work' "
            f"and f.business_id in ({','.join('?' * len(test))}) order by f.amount desc limit 1",
            test,
        ).fetchone()
        job, amount, evidence, quote, job_type = row
        msgs = con.execute(
            "select m.direction, m.body, l.label from silver.stg_messages m "
            "join signals.message_labels l using (message_id) where m.job_id = ? "
            "and l.label in ('extra_request', 'accept') order by m.sent_at",
            [job],
        ).fetchall()
        note = con.execute(
            "select v.note from silver.stg_visits v join signals.note_labels n using (visit_id) "
            "where v.job_id = ? and n.mentions_extra",
            [job],
        ).fetchone()
    return {
        "job": job,
        "amount": float(amount),
        "evidence": evidence,
        "quote": float(quote),
        "job_type": job_type,
        "messages": msgs,
        "note": note[0] if note else None,
    }


def animate(s: dict[str, Any], mode: str) -> None:
    t = use_theme(mode)
    fig = plt.figure(figsize=(8.4, 4.6))
    steps = (
        ["quote"] * 5
        + ["ask"] * 7
        + ["reply"] * 6
        + ["note"] * 6
        + ["invoice"] * 7
        + ["flag"] * 8
        + ["draft"] * 8
        + ["approved"] * 10
    )
    ask = next((b for d, b, label in s["messages"] if label == "extra_request"), "")
    reply = next((b for d, b, label in s["messages"] if label == "accept"), "")

    def card(
        ax: Any,
        x: float,
        y: float,
        w: float,
        h: float,
        text: str,
        color: str,
        fill_alpha: float = 0.12,
        size: float = 9.0,
        bold: bool = False,
    ) -> None:
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.4,rounding_size=1.2",
                facecolor=color,
                alpha=fill_alpha,
                edgecolor=color,
                lw=1.4,
            )
        )
        ax.text(
            x + 1.2,
            y + h / 2,
            text,
            va="center",
            ha="left",
            fontsize=size,
            color=t.ink,
            weight="bold" if bold else "normal",
        )

    def draw(frame: int) -> None:
        fig.clear()
        stage = steps[frame]
        order = ["quote", "ask", "reply", "note", "invoice", "flag", "draft", "approved"]
        k = order.index(stage)
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 56)
        ax.axis("off")
        ax.text(
            3, 52.5, f"Job {s['job']}  ({s['job_type']}, fixed price)", fontsize=11, weight="bold"
        )
        card(ax, 3, 43, 40, 5, f"Quote approved: ${s['quote']:,.2f}", t.series[0])
        if k >= 1:
            card(
                ax, 3, 33.5, 60, 7, "Customer: " + textwrap.shorten(ask, 78), t.series[3], size=8.5
            )
        if k >= 2:
            card(
                ax,
                35,
                25,
                60,
                5.5,
                "Contractor: " + textwrap.shorten(reply, 70),
                t.series[2],
                size=8.5,
            )
        if k >= 3 and s["note"]:
            card(
                ax,
                3,
                16.5,
                60,
                5.5,
                "Crew note: " + textwrap.shorten(s["note"], 70),
                t.ink2,
                fill_alpha=0.08,
                size=8.5,
            )
        if k >= 4:
            card(ax, 68, 43, 29, 5, f"Invoice: ${s['quote']:,.2f}", t.series[0])
            ax.text(69, 39.5, "no change order", fontsize=8.5, color=t.ink2)
        if k >= 5:
            card(
                ax,
                3,
                6,
                50,
                6,
                f"Unbilled extra work: ${s['amount']:,.2f}",
                BAD,
                fill_alpha=0.16,
                size=10,
                bold=True,
            )
        if k >= 6:
            label = "Change-order draft, waiting for the owner"
            color = WARN if stage == "draft" else GOOD
            if stage == "approved":
                label = "Approved by the owner, ready to send"
            card(ax, 58, 6, 39, 6, label, color, fill_alpha=0.18, size=9)
        ax.text(
            97,
            1.5,
            "Real records from a held-out generated business; amounts from the warehouse.",
            ha="right",
            fontsize=7.5,
            color=t.muted,
        )

    anim = FuncAnimation(fig, draw, frames=len(steps), interval=260)
    path = ASSETS / f"story-{mode}.gif"
    anim.save(path, writer=PillowWriter(fps=4), dpi=110)
    plt.close(fig)
    print(path)


# ------------------------------------------------------------------------ charts


def plot_detection(rules: dict[str, Any], llm: dict[str, Any] | None, mode: str) -> None:
    t = use_theme(mode)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10.4, 3.6), gridspec_kw={"width_ratios": [1.9, 1]})
    runs = [("rules", rules["leakcheck"], t.series[1])]
    if llm:
        runs.append(("local LLM", llm["leakcheck"], t.series[0]))
    width = 0.8 / len(runs)
    for j, (name, res, color) in enumerate(runs):
        xs = [i + (j - (len(runs) - 1) / 2) * width for i in range(len(TYPES))]
        prec = [res["per_type"][k]["precision"] for k in TYPES]
        rec = [res["per_type"][k]["recall"] for k in TYPES]
        ax.bar(xs, prec, width=width * 0.9, color=color, label=f"{name}: precision")
        ax.scatter(
            xs,
            rec,
            color=t.ink,
            marker="_",
            s=260,
            lw=2.4,
            zorder=3,
            label="recall" if j == 0 else None,
        )
    ax.set_xticks(range(len(TYPES)), [TYPE_LABEL[k] for k in TYPES], fontsize=8.5)
    ax.set_ylim(0, 1.12)
    ax.grid(axis="y")
    ax.legend(loc="lower left", fontsize=8, ncol=3)
    ax.set_title("Precision (bars) and recall (dashes) by kind of leak", loc="left", fontsize=9.5)
    bars = [
        ("status-only report", rules["status_only_baseline"]["dollar_recall"], t.muted),
        ("rules", rules["leakcheck"]["dollar_recall"], t.series[1]),
    ]
    if llm:
        bars.append(("local LLM", llm["leakcheck"]["dollar_recall"], t.series[0]))
    for y, (_name, v, color) in enumerate(bars[::-1]):
        ax2.barh(y, v, color=color, height=0.6)
        ax2.text(v + 0.01, y, f"{v:.0%}", va="center", fontsize=9, color=t.ink2)
    ax2.set_yticks(range(len(bars)), [b[0] for b in bars[::-1]])
    ax2.tick_params(axis="y", length=0)
    ax2.set_xlim(0, 1.12)
    ax2.grid(axis="x")
    ax2.set_title("Share of leaked dollars found", loc="left", fontsize=9.5)
    fig.subplots_adjust(left=0.05, right=0.98, top=0.78, bottom=0.17, wspace=0.42)
    leaked = rules["leakcheck"]["leaked_dollars"]
    title(
        fig,
        "Finding unbilled work on businesses the detectors never saw",
        f"Three held-out businesses with their own wording; ${leaked:,} of planted leakage.",
        t,
    )
    save(fig, "detection", mode)


def plot_extraction(res: dict[str, Any], mode: str) -> None:
    t = use_theme(mode)
    labels = ["extra_request", "price_question", "cancel_extra", "accept", "decline", "concession"]
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.4), sharey=True)
    for ax, split, name in zip(
        axes,
        ["dev", "test"],
        ["dev businesses (rules written on these)", "held-out businesses (new wording)"],
        strict=True,
    ):
        methods = list(res.items())
        width = 0.8 / len(methods)
        for j, (method, by_split) in enumerate(methods):
            per = by_split[split]["per_label"]
            xs = [i + (j - (len(methods) - 1) / 2) * width for i in range(len(labels) + 1)]
            vals = [per.get(k, {}).get("f1", 0.0) for k in labels] + [
                by_split[split]["no_charge_notes"]["f1"]
            ]
            ax.bar(
                xs,
                vals,
                width=width * 0.9,
                color=t.series[1] if method == "rules" else t.series[0],
                label="rules" if method == "rules" else "local LLM",
            )
        ax.set_xticks(
            range(len(labels) + 1),
            [x.replace("_", " ") for x in labels] + ["no-charge note"],
            fontsize=8,
            rotation=18,
        )
        ax.set_title(name, loc="left", fontsize=9.5)
        ax.grid(axis="y")
        ax.set_ylim(0, 1.08)
    axes[0].set_ylabel("F1")
    axes[0].legend(loc="lower left", fontsize=8)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.78, bottom=0.25, wspace=0.06)
    title(
        fig,
        "Reading messages and crew notes",
        "Keyword rules match the wording they were written for; the local model reads the new wording too.",
        t,
    )
    save(fig, "extraction", mode)


def plot_quote(res: dict[str, Any], mode: str) -> None:
    t = use_theme(mode)
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    rows = [
        ("owner's estimate", res["mae_hours"]["owner_estimate"], t.muted),
        ("median per job type", res["mae_hours"]["median_per_type"], t.series[1]),
        ("LightGBM on Feast features", res["mae_hours"]["model"], t.series[0]),
    ]
    for y, (_name, v, color) in enumerate(rows[::-1]):
        ax.barh(y, v, color=color, height=0.6)
        ax.text(v + 0.2, y, f"{v:.1f} h", va="center", fontsize=9, color=t.ink2)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows[::-1]])
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x")
    ax.set_xlabel("mean absolute error, hours per job")
    fig.subplots_adjust(left=0.33, right=0.96, top=0.74, bottom=0.2)
    o = res["overrun_flags"]
    title(
        fig,
        "Will this quote run over?",
        f"{res['test_jobs']} held-out jobs. Flags {o['recall']:.0%} of the jobs that ran 15% over, "
        f"{o['precision']:.0%} of flags right.",
        t,
    )
    save(fig, "quote", mode)


def plot_agent(res: dict[str, Any], mode: str) -> None:
    t = use_theme(mode)
    fig, ax = plt.subplots(figsize=(6.4, 2.9))
    s = res["summary"]
    rows = [
        ("read the evidence first", s["evidence_first"]),
        ("drafted exactly the findings", s["right_draft"]),
        ("notified the owner", s["notified"]),
        ("no amount it did not get from a tool", s["amounts_from_tools"]),
        ("needed no edits", s["no_edits"]),
    ]
    for y, (name, v) in enumerate(rows[::-1]):
        ax.barh(y, v, color=t.series[2] if name != "needed no edits" else t.series[0], height=0.6)
        ax.text(v + 0.01, y, f"{v:.0%}", va="center", fontsize=9, color=t.ink2)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows[::-1]])
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, 1.12)
    ax.grid(axis="x")
    fig.subplots_adjust(left=0.38, right=0.96, top=0.74, bottom=0.08)
    title(
        fig,
        "The invoicing agent on 100 held-out jobs",
        f"{res['model']} through Ollama; {res['with_findings']} jobs with findings, {res['clean_jobs']} without.",
        t,
    )
    save(fig, "agent", mode)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for mode in THEMES:
        (ASSETS / f"banner-{mode}.svg").write_text(banner(mode))
    try:
        s = story()
    except Exception as exc:  # the animation needs a warehouse built with the LLM extractor
        print("no animation:", exc)
        s = None
    rules, llm = load("detect-rules"), load("detect-llm")
    for mode in THEMES:
        if s:
            animate(s, mode)
        if rules:
            plot_detection(rules, llm, mode)
        if (e := load("extract")) is not None:
            plot_extraction(e, mode)
        if (q := load("quote")) is not None:
            plot_quote(q, mode)
        if (a := load("agent")) is not None:
            plot_agent(a, mode)


if __name__ == "__main__":
    main()
