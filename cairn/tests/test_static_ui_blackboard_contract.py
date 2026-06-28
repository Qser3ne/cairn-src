from __future__ import annotations

from pathlib import Path


STATIC_INDEX = Path(__file__).resolve().parents[1] / "src/cairn/server/static/index.html"


def read_index() -> str:
    return STATIC_INDEX.read_text(encoding="utf-8")


def test_static_ui_uses_current_finding_contract() -> None:
    html = read_index()

    legacy_finding_fields = [
        "finding.title",
        "finding.severity",
        "finding.status",
        "finding.vulnerability_type",
        "finding.target",
        "finding.location",
        "finding.impact",
        "finding.evidence",
        "finding.fact_id",
        "finding.created_at",
        "finding.intent_id",
    ]
    for field in legacy_finding_fields:
        assert field not in html

    for field in [
        "finding.description",
        "finding.creation_time",
        "finding.from",
        "finding.from_task",
        "finding.to",
        "finding.report",
    ]:
        assert field in html


def test_static_ui_selects_findings_as_independent_nodes() -> None:
    html = read_index()

    assert "selectedNode && selectedNode.type === 'finding'" in html
    assert "selectedFindingRecord()" in html
    assert "selectFinding(" in html
    assert "targetType: 'finding'" in html
    assert "centerGraphOnFact(finding.fact_id)" not in html


def test_static_ui_reads_origin_and_sorts_timeline_safely() -> None:
    html = read_index()

    assert "return this.project.origin" in html
    assert "const origin = this.project.origin" in html
    assert "a.timestamp.localeCompare(b.timestamp)" not in html
    assert "(a.timestamp || '').localeCompare(b.timestamp || '')" in html


def test_static_ui_detail_default_counts_collection_and_vulnerability_facts() -> None:
    html = read_index()

    assert "factCountByType(type)" in html
    assert "fact.type === type" in html
    assert "collectionFactCount()" in html
    assert "this.factCountByType('collection_fact')" in html
    assert "vulnerabilityFactCount()" in html
    assert "this.factCountByType('vulnerability_fact')" in html
    assert "${this.collectionFactCount()} collection facts" in html
    assert "${this.vulnerabilityFactCount()} vulnerability facts" in html
