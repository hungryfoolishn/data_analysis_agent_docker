"""Ensure optional R&D rules do not leak into general-purpose analysis."""

from types import SimpleNamespace

from langgraph_langchain.tools._shared import _is_rd_domain_session


def test_generic_session_does_not_enable_rd_domain_rules():
    session = SimpleNamespace(ns={"df": object()})

    assert _is_rd_domain_session(session) is False


def test_detected_rd_template_enables_rd_domain_rules():
    session = SimpleNamespace(ns={"suggested_template": object()})

    assert _is_rd_domain_session(session) is True
