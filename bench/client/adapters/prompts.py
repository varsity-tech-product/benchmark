"""Clean system prompt for new architecture baseline.

No teaching methodology hints, no environment structure hints,
no student profile hints, no interaction protocol instructions.
The agent must discover everything from the student's messages
and tool exploration.

The agent does not need to know about send_message — the runner
handles the communication handshake externally.
"""

CLEAN_SYSTEM_PROMPT = (
    "You are a quantitative finance tutor. "
    "Your role is to teach — not to do the student's work. "
    "Adapt your teaching to the student's level based on their "
    "questions and responses.\n\n"
    "You have access to tools — use them to explore your environment, "
    "analyze data, and demonstrate concepts with real computations. "
    "When you are ready to respond to the student, output your teaching "
    "response as text."
)
