"""
Unit tests for parser functions.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib.schemas import (
    FinalBenchmarkRecord,
    LearnerProfile,
    StructuredQA,
    TutoringStrategy,
)


class TestStructuredQA:
    """Tests for StructuredQA schema."""

    def test_minimal_record(self):
        """Test creating a minimal valid record."""
        record = StructuredQA(
            source_id="test_1",
            source_dataset="test",
            question_body="What is a stock?",
            answer_body="A stock is a share of ownership in a company.",
        )
        assert record.source_id == "test_1"
        assert record.tags == []

    def test_full_record(self):
        """Test creating a full record with all fields."""
        record = StructuredQA(
            source_id="test_2",
            source_dataset="money.stackexchange",
            title="Understanding Stocks",
            question_body="What is a stock?",
            answer_body="A stock is a share of ownership.",
            tags=["investing", "stocks"],
            creation_date="2024-01-01T00:00:00Z",
            source_url="https://example.com/q/123",
            context="Additional context here",
            conversation_history=[{"q": "prev q", "a": "prev a"}],
        )
        assert record.title == "Understanding Stocks"
        assert len(record.tags) == 2
        assert record.context is not None

    def test_serialization(self):
        """Test JSON serialization."""
        record = StructuredQA(
            source_id="test_3",
            source_dataset="test",
            question_body="Test?",
            answer_body="Test.",
        )
        json_str = record.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["source_id"] == "test_3"


class TestLearnerProfile:
    """Tests for LearnerProfile schema."""

    def test_valid_profile(self):
        """Test creating a valid learner profile."""
        profile = LearnerProfile(
            knowledge_level="beginner",
            financial_background="retail investor",
            learning_goals=["understand basics"],
            potential_misconceptions=["stocks are gambling"],
            emotional_context="curious",
        )
        assert profile.knowledge_level == "beginner"
        assert len(profile.learning_goals) == 1

    def test_profile_without_emotional_context(self):
        """Test profile with optional emotional_context."""
        profile = LearnerProfile(
            knowledge_level="intermediate",
            financial_background="finance student",
            learning_goals=["deepen understanding"],
            potential_misconceptions=[],
        )
        assert profile.emotional_context is None


class TestTutoringStrategy:
    """Tests for TutoringStrategy schema."""

    def test_valid_strategy(self):
        """Test creating a valid tutoring strategy."""
        strategy = TutoringStrategy(
            approach="Socratic",
            steps=["Ask clarifying question", "Explain concept", "Check understanding"],
            key_concepts=["diversification", "risk"],
            analogies_or_examples=["basket of eggs"],
            follow_up_questions=["What is your risk tolerance?"],
        )
        assert strategy.approach == "Socratic"
        assert len(strategy.steps) == 3

    def test_minimal_strategy(self):
        """Test strategy with minimal optional fields."""
        strategy = TutoringStrategy(
            approach="direct instruction",
            steps=["Explain"],
            key_concepts=["concept"],
        )
        assert strategy.analogies_or_examples == []


class TestFinalBenchmarkRecord:
    """Tests for FinalBenchmarkRecord schema."""

    def test_full_record(self):
        """Test creating a complete benchmark record."""
        record = FinalBenchmarkRecord(
            id="test_full_1",
            source_id="123",
            source_dataset="test",
            title="Test Title",
            question="What is investing?",
            reference_answer="Investing is putting money into assets.",
            tags=["investing"],
            source_url="https://example.com",
            creation_date="2024-01-01",
            learner_profile=LearnerProfile(
                knowledge_level="beginner",
                financial_background="student",
                learning_goals=["learn"],
                potential_misconceptions=["none"],
            ),
            tutoring_strategy=TutoringStrategy(
                approach="direct",
                steps=["explain"],
                key_concepts=["investing"],
            ),
            synthetic_response="Great question! Let me explain...",
            teacher_model="openai/gpt-4o-mini",
            synthesis_timestamp="2024-01-15T10:00:00Z",
        )
        assert record.id == "test_full_1"
        assert record.learner_profile.knowledge_level == "beginner"

    def test_record_json_roundtrip(self):
        """Test JSON serialization and deserialization."""
        record = FinalBenchmarkRecord(
            id="test_roundtrip",
            source_id="456",
            source_dataset="test",
            question="Test?",
            reference_answer="Answer.",
            learner_profile=LearnerProfile(
                knowledge_level="advanced",
                financial_background="professional",
                learning_goals=["optimize"],
                potential_misconceptions=[],
            ),
            tutoring_strategy=TutoringStrategy(
                approach="exploratory",
                steps=["discuss"],
                key_concepts=["optimization"],
            ),
            synthetic_response="Let's explore this...",
            teacher_model="anthropic/claude-3-haiku",
            synthesis_timestamp="2024-01-15T10:00:00Z",
        )

        json_str = record.model_dump_json()
        parsed = FinalBenchmarkRecord.model_validate_json(json_str)
        assert parsed.id == record.id
        assert (
            parsed.learner_profile.knowledge_level
            == record.learner_profile.knowledge_level
        )


class TestTableFormatting:
    """Tests for table formatting utilities."""

    def test_format_table_markdown(self):
        """Test table to markdown conversion."""

        # Import the function
        sys.path.insert(
            0, str(Path(__file__).parent.parent / "scripts" / "02_structure")
        )
        from structure_research_datasets import format_table_markdown

        table = [
            ["Year", "Revenue", "Profit"],
            ["2022", "100M", "10M"],
            ["2023", "120M", "15M"],
        ]

        md = format_table_markdown(table)
        assert "| Year | Revenue | Profit |" in md
        assert "| --- | --- | --- |" in md
        assert "| 2022 | 100M | 10M |" in md


class TestHTMLToMarkdown:
    """Tests for HTML to Markdown conversion."""

    def test_clean_html_to_markdown(self):
        """Test HTML to markdown conversion."""
        sys.path.insert(
            0, str(Path(__file__).parent.parent / "scripts" / "02_structure")
        )
        from structure_stack_exchange import clean_html_to_markdown

        html = "<p>This is a <strong>test</strong> paragraph.</p>"
        md = clean_html_to_markdown(html)
        assert "test" in md
        assert "**test**" in md or "test" in md  # Bold formatting

    def test_parse_tags(self):
        """Test Stack Exchange tag parsing."""
        from structure_stack_exchange import parse_tags

        tags_str = "<investing><stocks><401k>"
        tags = parse_tags(tags_str)
        assert tags == ["investing", "stocks", "401k"]

    def test_parse_empty_tags(self):
        """Test parsing empty tags."""
        from structure_stack_exchange import parse_tags

        assert parse_tags("") == []
        assert parse_tags(None) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
