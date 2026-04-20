"""Reusable widgets and helpers for the agendum TUI."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListView, ListItem

# ── colour maps ──────────────────────────────────────────────────────────

STATUS_STYLES: dict[str, str] = {
    "draft": "#888888",
    "open": "#60a5fa",
    "awaiting review": "#ffaa00",
    "changes requested": "#f87171",
    "review received": "#f59e0b",
    "approved": "#4ade80",
    "merged": "#888888",
    "review requested": "#a78bfa",
    "reviewed": "#7c6aad",
    "re-review requested": "#e879f9",
    "backlog": "#c7a17a",
    "in progress": "#2dd4bf",
    "closed": "#888888",
    "done": "#888888",
}

SECTION_ORDER: dict[str, tuple[str, str]] = {
    "pr_authored": ("MY PULL REQUESTS", "#ffaa00"),
    "pr_review": ("REVIEWS REQUESTED", "#a78bfa"),
    "issue": ("ISSUES & MANUAL", "#60a5fa"),
    "manual": ("ISSUES & MANUAL", "#60a5fa"),
}

# ── helper functions ─────────────────────────────────────────────────────


def styled_status(status: str) -> Text:
    """Return a Rich Text with the status coloured according to STATUS_STYLES."""
    colour = STATUS_STYLES.get(status, "#888888")
    return Text(status, style=colour)


def format_link(source: str, gh_number: int | None, gh_url: str | None) -> Text:
    """Format a clickable-style link column value."""
    if gh_number is not None:
        prefix = "PR" if source.startswith("pr") else "Issue"
        return Text(f"{prefix} #{gh_number}", style="bold #60a5fa", justify="right")
    return Text("—", style="#555555", justify="right")


def build_table_rows(tasks: list[dict]) -> list[tuple[str, list[dict]]]:
    """
    Group *tasks* by section in display order.

    Returns a list of ``(section_label, [task, ...])`` tuples.
    ``issue`` and ``manual`` sources are merged into a single section.
    """
    buckets: dict[str, list[dict]] = {
        "pr_authored": [],
        "pr_review": [],
        "issues_manual": [],
    }

    for task in tasks:
        source = task.get("source", "manual")
        if source == "pr_authored":
            buckets["pr_authored"].append(task)
        elif source == "pr_review":
            buckets["pr_review"].append(task)
        else:
            buckets["issues_manual"].append(task)

    result: list[tuple[str, list[dict]]] = []
    section_meta = [
        ("pr_authored", "MY PULL REQUESTS"),
        ("pr_review", "REVIEWS REQUESTED"),
        ("issues_manual", "ISSUES & MANUAL"),
    ]
    for key, label in section_meta:
        if buckets[key]:
            result.append((label, buckets[key]))
    return result


# ── ActionModal ──────────────────────────────────────────────────────────

_ACTION_LABELS: dict[str, str] = {
    "open_browser": "Open in browser",
    "remove": "Remove from board",
    "mark_reviewed": "Mark as reviewed",
    "mark_in_progress": "Mark in progress",
    "mark_backlog": "Move to backlog",
    "mark_done": "Mark done",
}


class _ActionListView(ListView):
    """ListView that dismisses the parent modal on escape/q."""

    def _key_escape(self) -> None:
        self.screen.dismiss(None)

    def key_q(self) -> None:
        self.screen.dismiss(None)

    def key_j(self) -> None:
        self.action_cursor_down()

    def key_k(self) -> None:
        self.action_cursor_up()


class ActionModal(ModalScreen[str | None]):
    """Context-aware action picker for a task."""

    DEFAULT_CSS = """
    ActionModal {
        align: center middle;
    }
    ActionModal > Vertical {
        width: 50;
        background: #1a1a2e;
        border: round #444;
        padding: 1 2;
    }
    ActionModal > Vertical > Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    ActionModal ListView {
        scrollbar-size: 0 0;
        height: auto;
    }
    """

    def __init__(self, task: dict) -> None:
        super().__init__()
        self._task = task

    def compose(self) -> ComposeResult:
        actions = self._build_actions()
        with Vertical():
            yield Label(self._task.get("title", "Task"))
            yield _ActionListView(
                *[ListItem(Label(label), id=action_id) for action_id, label in actions]
            )

    def _build_actions(self) -> list[tuple[str, str]]:
        source = self._task.get("source", "")
        status = self._task.get("status", "")
        actions: list[tuple[str, str]] = []
        if self._task.get("gh_url"):
            actions.append(("open_browser", _ACTION_LABELS["open_browser"]))
        if source == "pr_review":
            actions.append(("mark_reviewed", _ACTION_LABELS["mark_reviewed"]))
        if source == "manual":
            if status == "in progress":
                actions.append(("mark_backlog", _ACTION_LABELS["mark_backlog"]))
            else:
                actions.append(("mark_in_progress", _ACTION_LABELS["mark_in_progress"]))
            actions.append(("mark_done", _ACTION_LABELS["mark_done"]))
        actions.append(("remove", _ACTION_LABELS["remove"]))
        return actions

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        self.dismiss(item.id)
