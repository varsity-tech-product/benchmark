"""Pydantic models for the Quant Tutor Benchmark pipeline."""

from typing import Optional

from pydantic import BaseModel, Field


class StructuredQA(BaseModel):
    """Intermediate Q&A format after structuring raw data."""

    source_id: str = Field(description="Unique identifier from source dataset")
    source_dataset: str = Field(
        description="Name of source dataset (e.g., 'money.stackexchange', 'fiqa')"
    )
    title: Optional[str] = Field(
        default=None, description="Question title if available"
    )
    question_body: str = Field(description="Full question text in markdown")
    answer_body: str = Field(description="Full answer text in markdown")
    tags: list[str] = Field(default_factory=list, description="Topic tags")
    creation_date: Optional[str] = Field(
        default=None, description="ISO format date string"
    )
    source_url: Optional[str] = Field(
        default=None, description="URL to original source"
    )

    # Optional context fields for numerical reasoning datasets
    context: Optional[str] = Field(
        default=None, description="Additional context (tables, financial statements)"
    )
    conversation_history: Optional[list[dict]] = Field(
        default=None, description="For conversational datasets, prior turns"
    )


class LearnerProfile(BaseModel):
    """Synthesized learner profile for a question."""

    knowledge_level: str = Field(
        description="Estimated knowledge level: beginner, intermediate, advanced"
    )
    financial_background: str = Field(
        description="Inferred background (e.g., 'retail investor', 'finance professional')"
    )
    learning_goals: list[str] = Field(
        description="What the learner is trying to understand"
    )
    potential_misconceptions: list[str] = Field(
        description="Common misconceptions the learner might have"
    )
    emotional_context: Optional[str] = Field(
        default=None,
        description="Emotional state if detectable (e.g., 'anxious about retirement')",
    )


class TutoringStrategy(BaseModel):
    """Pedagogical strategy for responding to the learner."""

    approach: str = Field(
        description="Overall tutoring approach (e.g., 'Socratic', 'direct instruction')"
    )
    steps: list[str] = Field(description="3-5 step pedagogical plan")
    key_concepts: list[str] = Field(description="Core concepts to teach")
    analogies_or_examples: list[str] = Field(
        default_factory=list, description="Suggested analogies or examples to use"
    )
    follow_up_questions: list[str] = Field(
        default_factory=list, description="Questions to check understanding"
    )


class FinalBenchmarkRecord(BaseModel):
    """Complete record for the final benchmark dataset."""

    # Identifiers
    id: str = Field(description="Unique record ID")
    source_id: str = Field(description="Original source identifier")
    source_dataset: str = Field(description="Source dataset name")

    # Original Q&A
    title: Optional[str] = Field(default=None, description="Question title")
    question: str = Field(description="Original question text")
    reference_answer: str = Field(description="Original answer (ground truth)")

    # Metadata
    tags: list[str] = Field(default_factory=list, description="Topic tags")
    source_url: Optional[str] = Field(default=None, description="URL to original")
    creation_date: Optional[str] = Field(
        default=None, description="Original creation date"
    )

    # Optional context
    context: Optional[str] = Field(default=None, description="Additional context")
    conversation_history: Optional[list[dict]] = Field(
        default=None, description="Prior conversation turns"
    )

    # Synthesized components
    learner_profile: LearnerProfile = Field(description="Inferred learner profile")
    tutoring_strategy: TutoringStrategy = Field(description="Pedagogical strategy")
    synthetic_response: str = Field(description="LLM-generated tutoring response")

    # Synthesis metadata
    teacher_model: str = Field(description="Model used for response generation")
    profile_model: Optional[str] = Field(
        default=None, description="Model used for learner profile generation"
    )
    strategy_model: Optional[str] = Field(
        default=None, description="Model used for tutoring strategy generation"
    )
    synthesis_timestamp: str = Field(description="ISO timestamp of synthesis")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "mse_12345",
                "source_id": "12345",
                "source_dataset": "money.stackexchange",
                "title": "Should I pay off my mortgage early?",
                "question": "I have extra money each month...",
                "reference_answer": "The decision depends on several factors...",
                "tags": ["mortgage", "personal-finance"],
                "source_url": "https://money.stackexchange.com/questions/12345",
                "learner_profile": {
                    "knowledge_level": "beginner",
                    "financial_background": "retail investor",
                    "learning_goals": ["understand mortgage payoff tradeoffs"],
                    "potential_misconceptions": ["all debt is bad"],
                    "emotional_context": "anxious about long-term debt",
                },
                "tutoring_strategy": {
                    "approach": "Socratic",
                    "steps": ["Acknowledge concern", "Explain opportunity cost", "..."],
                    "key_concepts": ["opportunity cost", "interest rates"],
                    "analogies_or_examples": ["Compare to investing in index funds"],
                    "follow_up_questions": ["What is your current interest rate?"],
                },
                "synthetic_response": "Great question! Let me help you think through this...",
                "teacher_model": "openai/gpt-4o-mini",
                "synthesis_timestamp": "2024-01-15T10:30:00Z",
            }
        }
    }
