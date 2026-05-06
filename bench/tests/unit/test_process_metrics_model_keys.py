from eval.judges.process_metrics import evaluate_all_process_metrics


class FakeJudgeClient:
    def __init__(self):
        self.prompts = []

    def get_model_name(self):
        return "fake/judge"

    async def a_generate(self, prompt: str, **_kwargs):
        self.prompts.append(prompt)
        return (
            '{"score": 5, "reason": "good planning", "evidence": ["planned"]}',
            0.0,
        )


def test_qp_metrics_accept_injected_client_object():
    client = FakeJudgeClient()

    result = evaluate_all_process_metrics(
        task_description="Answer the task.",
        actual_output="Done.",
        proxy_logs=[],
        category="conceptual_qa",
        conversation=[{"role": "assistant", "content": "I planned and answered."}],
        model=client,
        reference_trace={"step_count": 1},
        required_capabilities=["answer the task"],
        tool_usage_result={"score": 1.0, "reason": "no tools needed"},
    )

    assert client.prompts
    assert result["task_planning"]["score"] == 1.0
    assert result["problem_solving"]["skipped"] is True
    assert result["aggregate_process_score"] == 1.0
    assert result["_weights_used"] == {
        "tool_usage": 0.20,
        "action_economy": 0.15,
        "task_planning": 0.25,
        "problem_solving": 0.25,
    }
    assert "code_lifecycle" not in result
    assert result["_weights_effective"] == {
        "tool_usage": 0.3333,
        "action_economy": 0.25,
        "task_planning": 0.4167,
    }
