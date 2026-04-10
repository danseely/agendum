"""Tests for widget helpers and ActionModal."""

import pytest
from rich.text import Text

from agendum.widgets import (
    ActionModal,
    build_table_rows,
    format_link,
    styled_status,
)


# ── styled_status ────────────────────────────────────────────────────────


class TestStyledStatus:
    def test_known_status_gets_colour(self) -> None:
        result = styled_status("approved")
        assert isinstance(result, Text)
        assert str(result) == "approved"
        assert result.style == "#4ade80"

    def test_unknown_status_gets_fallback_colour(self) -> None:
        result = styled_status("mystery")
        assert str(result) == "mystery"
        assert result.style == "#888888"

    @pytest.mark.parametrize(
        ("status", "expected_colour"),
        [
            ("draft", "#888888"),
            ("open", "#60a5fa"),
            ("awaiting review", "#ffaa00"),
            ("changes requested", "#f87171"),
            ("merged", "#888888"),
            ("review requested", "#a78bfa"),
            ("re-review requested", "#a78bfa"),
            ("in progress", "#60a5fa"),
            ("active", "#60a5fa"),
            ("done", "#888888"),
        ],
    )
    def test_all_known_statuses(self, status: str, expected_colour: str) -> None:
        assert styled_status(status).style == expected_colour


# ── format_link ──────────────────────────────────────────────────────────


class TestFormatLink:
    def test_pr_link(self) -> None:
        result = format_link("pr_authored", 42, "https://github.com/org/repo/pull/42")
        assert str(result) == "PR #42"

    def test_pr_review_link(self) -> None:
        result = format_link("pr_review", 7, "https://github.com/org/repo/pull/7")
        assert str(result) == "PR #7"

    def test_issue_link(self) -> None:
        result = format_link("issue", 99, "https://github.com/org/repo/issues/99")
        assert str(result) == "Issue #99"

    def test_manual_task_shows_dash(self) -> None:
        result = format_link("manual", None, None)
        assert str(result) == "\u2014"  # em dash

    def test_link_right_aligned(self) -> None:
        result = format_link("pr_authored", 1, "https://github.com/org/repo/pull/1")
        assert result.justify == "right"


# ── build_table_rows ─────────────────────────────────────────────────────


class TestBuildTableRows:
    def test_groups_by_section(self) -> None:
        tasks = [
            {"source": "pr_authored", "title": "PR A"},
            {"source": "pr_review", "title": "Review B"},
            {"source": "issue", "title": "Issue C"},
            {"source": "manual", "title": "Manual D"},
        ]
        sections = build_table_rows(tasks)
        labels = [label for label, _ in sections]
        assert labels == ["MY PULL REQUESTS", "REVIEWS REQUESTED", "ISSUES & MANUAL"]

    def test_issues_and_manual_merged(self) -> None:
        tasks = [
            {"source": "issue", "title": "Issue"},
            {"source": "manual", "title": "Manual"},
        ]
        sections = build_table_rows(tasks)
        assert len(sections) == 1
        assert sections[0][0] == "ISSUES & MANUAL"
        assert len(sections[0][1]) == 2

    def test_empty_sections_omitted(self) -> None:
        tasks = [{"source": "pr_review", "title": "Review only"}]
        sections = build_table_rows(tasks)
        assert len(sections) == 1
        assert sections[0][0] == "REVIEWS REQUESTED"

    def test_empty_input(self) -> None:
        assert build_table_rows([]) == []

    def test_preserves_task_order_within_section(self) -> None:
        tasks = [
            {"source": "pr_authored", "title": "First"},
            {"source": "pr_authored", "title": "Second"},
            {"source": "pr_authored", "title": "Third"},
        ]
        sections = build_table_rows(tasks)
        titles = [t["title"] for t in sections[0][1]]
        assert titles == ["First", "Second", "Third"]

    def test_unknown_source_goes_to_issues_manual(self) -> None:
        tasks = [{"source": "unknown_type", "title": "Mystery"}]
        sections = build_table_rows(tasks)
        assert sections[0][0] == "ISSUES & MANUAL"


# ── ActionModal ──────────────────────────────────────────────────────────


class TestActionModal:
    def test_pr_authored_actions(self) -> None:
        task = {"source": "pr_authored", "gh_url": "https://github.com/org/repo/pull/1", "title": "PR"}
        modal = ActionModal(task)
        actions = modal._build_actions()
        action_ids = [a[0] for a in actions]
        assert "open_browser" in action_ids
        assert "remove" in action_ids
        assert "mark_reviewed" not in action_ids
        assert "mark_done" not in action_ids

    def test_pr_review_has_mark_reviewed(self) -> None:
        task = {"source": "pr_review", "gh_url": "https://github.com/org/repo/pull/1", "title": "Review"}
        modal = ActionModal(task)
        actions = modal._build_actions()
        action_ids = [a[0] for a in actions]
        assert "mark_reviewed" in action_ids
        assert "mark_done" not in action_ids

    def test_manual_has_mark_done(self) -> None:
        task = {"source": "manual", "title": "Task"}
        modal = ActionModal(task)
        actions = modal._build_actions()
        action_ids = [a[0] for a in actions]
        assert "mark_done" in action_ids
        assert "mark_reviewed" not in action_ids

    def test_no_gh_url_omits_open_browser(self) -> None:
        task = {"source": "manual", "title": "No URL"}
        modal = ActionModal(task)
        actions = modal._build_actions()
        action_ids = [a[0] for a in actions]
        assert "open_browser" not in action_ids

    def test_remove_always_present(self) -> None:
        for source in ("pr_authored", "pr_review", "issue", "manual"):
            task = {"source": source, "title": "Test"}
            modal = ActionModal(task)
            action_ids = [a[0] for a in modal._build_actions()]
            assert "remove" in action_ids
