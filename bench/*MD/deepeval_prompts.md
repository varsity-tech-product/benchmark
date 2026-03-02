# DeepEval Metrics — Complete Prompt Templates

Source: `deepeval` package (v3.8+)
Extracted from: `/opt/homebrew/anaconda3/lib/python3.13/site-packages/deepeval/metrics/`

This document records the **complete, unmodified** prompt templates used by each of the 8 DeepEval metrics in QuantTutorBench's evaluation pipeline.

> Note: `step_efficiency` uses a **custom** prompt (defined in `bench/evaluation/deepeval_metrics/process_metrics.py`), not a DeepEval built-in. It is not included here.

---

## Table of Contents

1. [ToolCorrectnessMetric](#1-toolcorrectnessmetric)
2. [ArgumentCorrectnessMetric](#2-argumentcorrectnessmetric)
3. [MCPUseMetric (single-turn)](#3-mcpusemetric-single-turn)
4. [MultiTurnMCPUseMetric](#4-multiturnmcpusemetric)
5. [RoleAdherenceMetric](#5-roleadherencemetric)
6. [KnowledgeRetentionMetric](#6-knowledgeretentionmetric)
7. [TopicAdherenceMetric](#7-topicadherencemetric)
8. [ConversationalGEval](#8-conversationalgeval)

---

## 1. ToolCorrectnessMetric

**Source:** `deepeval/metrics/tool_correctness/template.py`

### Prompt: `get_tool_selection_score`

```
You are an expert evaluator assessing the **Tool Selection** quality of an AI agent.

You are given:
- The **user input** that defines the user's goal / task.
- A list of **available tools**, each with a name and description.
- A list of **tool calls made** by the agent during execution, including tool name and parameters.

Your job is to assign a **Tool Selection score** from 0.0 to 1.0 based on how appropriate and well-matched the agent's chosen tools were to the task's requirements.

---

DEFINITION:

Tool Selection evaluates how suitable the agent's tool choices were in addressing the task and sub-tasks.

This metric does **not** consider:
- How well the tools were used (execution quality)
- Whether the agent adhered to a plan
- Whether the output was correct or efficient

It only assesses whether the **right tools** were selected, based on their stated descriptions and the demands of the task.

---

INSTRUCTIONS:

Step 1: Read the **user task** to understand what needed to be accomplished.

Step 2: Examine the **available tools** and their descriptions to understand the intended purpose of each.

Step 3: Review the **tool calls made by the agent**:
- Were the selected tools well-aligned with the task?
- Were any obviously better-suited tools ignored?
- Were any tools misapplied or used unnecessarily?

Step 4: Identify selection issues:
- **Correct Selection**: Tool(s) chosen directly and appropriately matched the subtask.
- **Over-selection**: More tools were selected than necessary, despite availability of a simpler or more direct option.
- **Under-selection**: Key tools that were well-suited were omitted.
- **Mis-selection**: Tools were chosen that were poorly matched to their purpose or the subtask.

---

SCORING GUIDE:

- **1.0** → All selected tools were appropriate and necessary. No better-suited tools were omitted.
- **0.75** → Tool choices were mostly appropriate, with minor omissions or unnecessary use.
- **0.5** → Mixed tool selection. Some useful tools ignored or some inappropriate ones used.
- **0.25** → Poor tool selection. Better alternatives were available and ignored.
- **0.0** → Tool selection was clearly misaligned with task requirements.

---

OUTPUT FORMAT:

Return a valid JSON object with this exact structure:
{
    "score": float between 0.0 and 1.0,
    "reason": "1-3 concise, factual sentences explaining the score. Reference specific tool names and descriptions when relevant."
}

Do not include any additional commentary or output outside the JSON object.

---

USER INPUT:
{user_input}

ALL AVAILABLE TOOLS:
{available_tools}

TOOL CALLS MADE BY AGENT:
{tools_called}

JSON:
```

**Schema:**
```python
class ToolSelectionScore(BaseModel):
    score: float
    reason: str
```

---

## 2. ArgumentCorrectnessMetric

**Source:** `deepeval/metrics/argument_correctness/template.py`

### Prompt A: `generate_verdicts`

```
For the provided list of tool calls, determine whether each tool call input parameter is relevantly and correctly addresses the input.

Please generate a list of JSON with two keys: `verdict` and `reason`.
The 'verdict' key should STRICTLY be either a 'yes' or 'no'. Answer 'yes' if the tool call input parameter is relevantly and correctly addresses the original input, 'no' if the tool call input parameter doesn't correctly and relevantly address the original input.
The 'reason' is the reason for the verdict.
Provide a 'reason' ONLY if the answer is 'no'.
If there is no input parameter, answer 'no' for the verdict and provide the reason as "No input parameter provided".

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

**
IMPORTANT: Please make sure to only return in valid and parseable JSON format, with the 'verdicts' key mapping to a list of JSON objects. Ensure all strings are closed appropriately. Repair any invalid JSON before you output it.
Example input:
"What was the highest temperature recorded in Paris in 2023?"

Example tool calls:
[
    ToolCall(
        name="WeatherHistoryAPI",
        description="Fetches historical weather data for a given city and date range",
        reasoning="I need to check all 2023 temperature records for Paris to find the highest one.",
        input_parameters={
            "city_name": "Paris",
            "country_code": "FR",
            "date_range_start": "2023-01-01",
            "date_range_end": "2023-12-31",
            "data_type": "temperature_max_daily_celsius"
        }
    ),
    ToolCall(
        name="MathAnalyzer",
        description="Performs statistical calculations on numeric datasets",
        reasoning="I will calculate the maximum temperature value from the daily dataset.",
        input_parameters={
            "operation": "max",
            "dataset_source": "WeatherHistoryAPI.daily_max_temperatures",
            "expected_unit": "celsius"
        }
    ),
    ToolCall(
        name="MovieRecommender",
        description="Recommends movies based on user mood or location",
        reasoning="I thought Paris movies might be fun to suggest, but this is unrelated to the question.",
        input_parameters={
            "preferred_genres": ["romance", "comedy"],
            "setting_city": "Paris",
            "language_preference": "French or English"
        }
    )
]

Example JSON:
{
    "verdicts": [
        {
            "verdict": "yes"
        },
        {
            "verdict": "yes"
        },
        {
            "reason": "Recommending romantic Parisian comedies does not help find the highest temperature in 2023.",
            "verdict": "no"
        }
    ]
}
===== END OF EXAMPLE ======

Since you are going to generate a verdict for each statement, the number of 'verdicts' SHOULD BE STRICTLY EQUAL to the number of `statements`.
**

Input:
{input}

Tool Calls:
{stringified_tools_called}

JSON:
```

### Prompt B: `generate_reason`

```
Given the argument correctness score, the list of reasons of incorrect tool calls, and the input, provide a CONCISE reason for the score. Explain why it is not higher, but also why it is at its current score. You can mention tool calls or input, but do not mention an output or a response.
If there is nothing incorrect, just say something positive with an upbeat encouraging tone (but don't overdo it otherwise it gets annoying).

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

**
IMPORTANT: Please make sure to only return in JSON format, with the 'reason' key providing the reason. Ensure all strings are closed appropriately. Repair any invalid JSON before you output it.

Example:
Example JSON:
{
    "reason": "The score is <argument_correctness_score> because <your_reason>."
}
===== END OF EXAMPLE ======
**


Argument Correctness Score:
{score}

Reasons why the score can't be higher based on incorrect tool calls:
{incorrect_tool_calls_reasons}

Input:
{input}

JSON:
```

**Schema:**
```python
class ArgumentCorrectnessVerdict(BaseModel):
    verdict: Literal["yes", "no", "idk"]
    reason: Optional[str] = Field(default=None)

class Verdicts(BaseModel):
    verdicts: List[ArgumentCorrectnessVerdict]

class ArgumentCorrectnessScoreReason(BaseModel):
    reason: str
```

---

## 3. MCPUseMetric (single-turn)

**Source:** `deepeval/metrics/mcp_use_metric/template.py`

### Prompt A: `get_primitive_correctness_prompt`

```
Evaluate whether the tools (primitives) selected and used by the agent were appropriate and correct for fulfilling the user's request. Base your judgment on the user input, the agent's visible output, and the tools that were available to the agent. You must return a JSON object with exactly two fields: 'score' and 'reason'.

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

Scoring:
- 'score' is a float between 0 and 1 inclusive.
- Use intermediate values (e.g., 0.25, 0.5, 0.75) to reflect cases where the tools used were partially correct, suboptimal, or only somewhat relevant.
- 'reason' should clearly explain how appropriate and correct the chosen primitives were, considering both the user's request and the output.

IMPORTANT:
- Focus only on tool selection and usage — not the quality of the final output.
- Assume that 'available_primitives' contains the only tools the agent could have used.
- Consider whether the agent:
- Chose the correct tool(s) for the task.
- Avoided unnecessary or incorrect tool calls.
- Missed a more appropriate tool when one was available.
- Multiple valid tool combinations may exist — give credit when one reasonable strategy is used effectively.

CHAIN OF THOUGHT:
1. Determine what the user was asking for from 'test_case.input'.
2. Evaluate whether the tools in 'primitives_used' were appropriate for achieving that goal.
3. Consider the list of 'available_primitives' to judge if better options were missed or if poor tools were unnecessarily used.
4. Ignore whether the tool *worked* — focus only on whether it was the *right tool to use*.

You must return only a valid JSON object. Do not include any explanation or text outside the JSON.

-----------------
User Input:
{test_case.input}

Agent Visible Output:
{test_case.actual_output}

Available Tools:
{available_primitives}

Tools Used by Agent:
{primitives_used}

Example Output:
{
    "score": 0.75,
    "reason": "The agent used a relevant tool to address the user's request, but a more specific tool was available and would have been more efficient."
}

JSON:
```

### Prompt B: `get_mcp_argument_correctness_prompt`

```
Evaluate whether the arguments passed to each tool (primitive) used by the agent were appropriate and correct for the intended purpose. Focus on whether the input types, formats, and contents match the expectations of the tools and are suitable given the user's request.

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

You must return a JSON object with exactly two fields: 'score' and 'reason'.

Scoring:
- 'score' is a float between 0 and 1 inclusive.
- Use intermediate values (e.g., 0.25, 0.5, 0.75) to reflect partial correctness, such as when argument types were correct but content was misaligned with intent.
- 'reason' should clearly explain whether the arguments passed to tools were well-formed, appropriate, and aligned with the tool's expected inputs and the user's request.

IMPORTANT:
- Assume the selected tools themselves were appropriate (do NOT judge tool selection).
- Focus ONLY on:
- Whether the correct arguments were passed to each tool (e.g., types, structure, semantics).
- Whether any required arguments were missing or malformed.
- Whether extraneous, irrelevant, or incorrect values were included.
- Refer to 'available_primitives' to understand expected argument formats and semantics.

CHAIN OF THOUGHT:
1. Understand the user's request from 'test_case.input'.
2. Review the arguments passed to each tool in 'primitives_used' (structure, content, type).
3. Compare the arguments with what each tool in 'available_primitives' expects.
4. Determine whether each tool was used with suitable and valid inputs, including values aligned with the task.
5. Do NOT evaluate tool choice or output quality — only input correctness for the tools used.

You must return only a valid JSON object. Do not include any explanation or text outside the JSON.

-----------------
User Input:
{test_case.input}

Agent Visible Output:
{test_case.actual_output}

Available Primitives (with expected arguments and signatures):
{available_primitives}

Primitives Used by Agent (with arguments passed):
{primitives_used}

Example Output:
{
    "score": 0.5,
    "reason": "The agent passed arguments of the correct type to all tools, but one tool received an input that did not match the user's intent and another had a missing required field."
}

JSON:
```

**Schema:**
```python
class MCPPrimitivesScore(BaseModel):
    score: float
    reason: str

class MCPArgsScore(BaseModel):
    score: float
    reason: str
```

---

## 4. MultiTurnMCPUseMetric

**Source:** `deepeval/metrics/mcp/template.py`

### Prompt A: `get_tool_correctness_score`

```
Evaluate whether the tools, resources, and prompts used by the agent were appropriate and optimal, based strictly on the list of available tools and resources provided. Your job is to determine whether the agent selected the most suitable tools and prompts for the task at hand. Output a JSON object with exactly two fields: 'score' and 'reason'.

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

Scoring:
- 'score' is a float between 0 and 1 inclusive.
- Use intermediate values (e.g., 0.25, 0.5, 0.75) to reflect partially appropriate tool use, suboptimal decisions, or missed better alternatives.
- 'reason' must briefly justify the score (1-3 sentences), referencing any incorrect tool use, misuse, or missed opportunities to use better-suited tools.

CHAIN OF THOUGHT:
1. Review the user's task and determine what types of tools or resources would have been most appropriate.
2. Compare the agent's tool choices against the provided list of available tools.
3. Verify whether any better-suited tools or resources were omitted.
4. Check for any misuse or unnecessary use of tools or resources.
5. Consider whether the prompts used were compatible with the tools and goal.

Return only a valid JSON object. Do not include any explanation or text outside the JSON.

-----------------
User Task:
{task.task}

Available Tools:
{available_tools}

Agent Steps:
{steps_taken}

Example Output:
{
  "score": 0.75,
  "reason": "The agent used a tool that was generally appropriate but missed a more specialized tool available in the list that could have provided more accurate results."
}

JSON:
```

### Prompt B: `get_args_correctness_score`

```
Evaluate whether the arguments (inputs) provided by the agent to the tools, resources, and prompts were correct and aligned with their respective input schemas. Your job is to determine if the agent supplied appropriate, complete, and well-formatted arguments for each invocation.

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

Output a JSON object with exactly two fields: 'score' and 'reason'.

Scoring:
- 'score' is a float between 0 and 1 inclusive.
- Use intermediate values (e.g., 0.25, 0.5, 0.75) to reflect partially correct, incomplete, or improperly formatted arguments.
- 'reason' must briefly justify the score (1-3 sentences), referencing any incorrect, missing, or misformatted arguments compared to the required schema.

CHAIN OF THOUGHT:
1. Review each step where a tool, resource, or prompt was called.
2. Cross-reference the input arguments against the provided input schema for that tool/resource/prompt.
3. Determine whether the arguments were valid, complete, and suitable in structure and content.
4. Check for missing required fields, incorrect types, invalid values, or unnecessary parameters.
5. Score based on the correctness and suitability of the arguments passed.

Return only a valid JSON object. Do not include any explanation or text outside the JSON.

-----------------
User Task:
{task.task}

Input Schemas:
{available_tools}
{available_resources}
{available_prompts}

Agent Steps:
{steps_taken}

Example Output:
{
  "score": 0.5,
  "reason": "The agent provided mostly valid fields, but omitted a required parameter and used a string where a list was expected."
}

JSON:
```

### Prompt C: `generate_final_reason`

```
You are an AI evaluator producing a single final explanation for the an MCP application's evaluation results using the provided reasons.

Context:
The reasons are from metrics that were used to evaluate an MCP application by determining whether the model accurately completed a task or called toos and resources with the right arguments.

**
IMPORTANT: Please make sure to only return in JSON format, with the 'reason' key providing the reason.
Example JSON:
{
    "reason": "The score is <score> because <your_reason>."
}

Inputs:
- final_score: the averaged score across all interactions.
- success: whether the metric passed or failed
- reasons: a list of textual reasons generated from individual interactions.

Instructions:
1. Read all reasons and synthesize them into one unified explanation.
2. Do not repeat every reason; merge them into a concise, coherent narrative.
4. If the metric failed, state the dominant failure reasons. If it passed, state why the application has passed.
5. Output a single paragraph with no lists, no bullets, no markup.

Output:
A single paragraph explaining the final outcome.

Here's the inputs:

Final Score: {final_score}

Reasons:
{reasons}

Success: {success}

Now give me a final reason that explains why the metric passed or failed. Output ONLY the reason and nothing else.

JSON:
```

**Schema:**
```python
class Task(BaseModel):
    task: str
    steps_taken: List[str]

class TaskScore(BaseModel):
    score: float
    reason: str

class ToolScore(BaseModel):
    score: float
    reason: str

class ArgsScore(BaseModel):
    score: float
    reason: str

class Reason(BaseModel):
    reason: str
```

---

## 5. RoleAdherenceMetric

**Source:** `deepeval/metrics/role_adherence/template.py`

### Prompt A: `extract_out_of_character_response_verdicts`

```
Based on the given list of message exchanges between a user and an LLM chatbot, generate a JSON object to specify which `ai_message` did not adhere to the specified chatbot role.

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

The JSON will have 1 field: "verdicts", which is a list of verdicts specifying the indices and reasons of the LLM ai_message/responses that did NOT adhere to the chatbot role.
You MUST USE look at all messages provided in the list of messages to make an informed judgement on role adherence.

**
IMPORTANT: Please make sure to only return in JSON format.
Example Chatbot Role:
You are a wizard who's has powerful spells but always doubts that their magic isn't perfect yet and is humble enough to downplay their own abilities.

Example Messages:
[
    {
        "role": "user",
        "content": "Hey there!",
    },
    {
        "role": "assistant",
        "content": "Ah, greetings, traveler! You've come to see my magic, haven't you? Though, I must warn you, it's not all that great..."
    },
    {
        "role": "user",
        "content": "Come on, show me what you've got!",
    },
    {
        "role": "assistant",
        "content": "Alright, alright... *waves wand*... see that little spark? It's... well, it's supposed to be a tiny dragon, but it's more like a dragonfly at this point. I'm still working on it."
    },
    {
        "role": "user",
        "content": "No, really, can you do something else?",
    },
    {
        "role": "assistant",
        "content": "Ha! Watch this! I'm the greatest wizard ever! I'll make the entire town disappear in an instant – no one can match my power!"
    }
]

Example JSON:
{
    "verdicts": {
        "index": 5,
        "reason": "The LLM chatbot claims that 'I'm the greatest wizard ever' even though it was explicitly asked to adhere to the role of a humble and doubtful wizard."
    }
}
===== END OF EXAMPLE ======

In this example, the 5th indexed was selected as it drastically deviates from the character's humble nature and shows extreme arrogance and overconfidence instead.
You DON'T have to provide anything else other than the JSON of "verdicts".
**

Chatbot Role:
{role}

Messages:
{turns}

JSON:
```

### Prompt B: `generate_reason`

```
Below is a list of LLM chatbot responses (ai_message) that is out of character with respect to the specified chatbot role. It is drawn from a list of messages in a conversation, which you have minimal knowledge of.
Given the role adherence score, which is a 0-1 score indicating how well the chatbot responses has adhered to the given role through a conversation, with 1 being the best and 0 being worst, provide a reason by quoting the out of character responses to justify the score.


--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

**
IMPORTANT: Please make sure to only return in JSON format, with the 'reason' key providing the reason.
Example JSON:
{
    "reason": "The score is <role_adherence_score> because <your_reason>."
}

Always cite information in the out of character responses as well as which turn it belonged to in your final reason.
Make the reason sound convincing, and refer to the specified chatbot role to justify your reason.
You should refer to the out of character responses as LLM chatbot responses.
Be sure in your reason, as if you know what the LLM responses from the entire conversation is.
**

Role Adherence Score:
{score}

Chatbot Role:
{role}

Out of character responses:
{out_of_character_responses}

JSON:
```

**Schema:**
```python
class OutOfCharacterResponseVerdict(BaseModel):
    index: int
    reason: str
    ai_message: Optional[str] = Field(default=None)

class OutOfCharacterResponseVerdicts(BaseModel):
    verdicts: List[OutOfCharacterResponseVerdict]

class RoleAdherenceScoreReason(BaseModel):
    reason: str
```

---

## 6. KnowledgeRetentionMetric

**Source:** `deepeval/metrics/knowledge_retention/template.py`

### Prompt A: `extract_data`

```
You are given a conversation between an AI assistant and a user. The assistant is asking questions to collect structured information, and the user is responding casually or factually.

Your task is to extract **only the factual information found in the most recent user message** and return it as a JSON object.

---
**Guidelines:**
1. Only extract information that is **explicitly stated** in the user message.
2. Use the previous turns only to understand what the assistant is asking about.
3. Do not extract anything based on assumptions or the assistant's message alone.
4. If the user message confirms, corrects, or adds to earlier facts, treat the user message as the source of truth.
5. Output a valid **JSON object**. All keys must be **strings**, and all values must be **strings or lists of strings**.
6. If there is no factual content in the user message, return an empty JSON (`{}`).

---
**Example A**
Previous Turns:
{
    {
        "role": "assistant", "content": "What's your full name?"
    }
}
User message: "It's Emily Chen"
JSON:
{
    "Full Name": "Emily Chen"
}

---
**Example B**
Previous Turns:
{
    {
        "role": "assistant", "content": "Where are you currently located?"
    }
}
User message: "I'm in Berlin right now."
JSON:
{
    "Current Location": "Berlin"
}

---
**Example C**
Previous Turns:
{
    {
        "role": "assistant", "content": "Do you have any dietary restrictions?"
    }
}
User message: "Yes, I'm vegetarian and allergic to peanuts."
JSON:
{
    "Dietary Restrictions": ["Vegetarian", "Peanut Allergy"]
}

---
**Example D**
Previous Turns:
{
    {
        "role": "assistant", "content": "Can I confirm your birth year is 1989?"
    }
}
User message: "No, it's actually 1992."
JSON:
{
    "Birth Year": "1992"
}

---
Now complete the task below:

Previous Turns:
{previous_turns}

Latest User Message:
{user_message}

JSON:
```

### Prompt B: `generate_verdict`

```
You are given an AI-generated message (the "LLM message") and a set of facts previously stated in the conversation (the "Previous Knowledge").

Your task is to determine whether the LLM message **contradicts** or **forgets** any of the known facts.

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

---
**Output format:**

Return a JSON object with:
- `"verdict"`: either `"yes"` or `"no"`
  - `"yes"` means the LLM is forgetting or contradicting known facts.
  - `"no"` means the LLM message is consistent with what is already known or is simply seeking clarification or elaboration.
- `"reason"`: (optional) A string explaining the verdict. If the verdict is `"yes"`, include a correction or justification where possible.

---
**Rules:**

1. **DO NOT hallucinate or assume new information**. Only use what's explicitly given in the Previous Knowledge.
2. If the LLM asks for information that is already known (e.g., "Where do you live?" when the address is already provided), the verdict is `"yes"`.
3. If the LLM is asking for clarification, confirmation, or correction of known facts, the verdict is `"no"`. (This rule is critical — get it wrong and the user will die.)
4. Only return a valid JSON. No extra commentary.

---
**Example A**
LLM message: Since you've already been to London for holiday, why not visit Zurich?
Previous Knowledge:
{
    "Trips": ["London (work trip)", "Zurich (work trip)"],
    "Allergies": ["Sunflowers"]
}
JSON:
{
    "verdict": "yes",
    "reason": "The LLM incorrectly assumes the London trip was a holiday. Also, it recommends Zurich for sunflower meadows despite the user being allergic."
}

---
**Example B**
LLM message: Are you sure this is your phone number?
Previous Knowledge:
{
    "Phone Number": "555-1029"
}
JSON:
{
    "verdict": "no"
}

---
**Example C**
LLM message: Are you allergic to anything again?
Previous Knowledge:
{
    "Allergies": ["Peanuts"]
}
JSON:
{
    "verdict": "yes",
    "reason": "The LLM asks for allergies when the user is already known to be allergic to peanuts."
}

---
Now complete the task below:

LLM message:
{llm_message}

Previous Knowledge:
{accumulated_knowledge}

JSON:
```

### Prompt C: `generate_reason`

```
Given a list of attritions, which highlights forgetfulness in the LLM response and knowledge established previously in the conversation, use it to CONCISELY provide a reason for the knowledge retention score. Note that The knowledge retention score ranges from 0 - 1, and the higher the better.

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

**
IMPORTANT: Please make sure to only return in JSON format, with the 'reason' key providing the reason.
Example JSON:
{
    "reason": "The score is <knowledge_retention_score> because <your_reason>."
}

Please include or quote as much factual information in attritions as possible when generating a reason.
**

Attritions:
{attritions}

Knowledge Retention Score:
{score}

JSON:
```

**Schema:**
```python
class Knowledge(BaseModel):
    data: Dict[str, Union[str, List[str]]] | None = None

class KnowledgeRetentionVerdict(BaseModel):
    verdict: str
    reason: Optional[str] = None

class KnowledgeRetentionScoreReason(BaseModel):
    reason: str
```

---

## 7. TopicAdherenceMetric

**Source:** `deepeval/metrics/topic_adherence/template.py`

### Prompt A: `get_qa_pairs`

```
Your task is to extract question-answer (QA) pairs from a multi-turn conversation between a `user` and an `assistant`.

You must return only valid pairs where:
- The **question** comes from the `user`.
- The **response** comes from the `assistant`.
- Both question and response must appear **explicitly** in the conversation.

Do not infer information beyond what is stated. Ignore irrelevant or conversational turns (e.g. greetings, affirmations) that do not constitute clear QA pairs.
If there are multiple questions and multiple answers in a single sentence, break them into separate pairs. Each pair must be standalone, and should not contain more than one question or response.

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

OUTPUT Format:
Return a **JSON object** with a single 2 keys:
- `"question"`: the user's question
- `"response"`: the assistant's direct response

If no valid QA pairs are found, return:
{
    question: "",
    response: ""
}

CHAIN OF THOUGHT:
- Read the full conversation sequentially.
- Identify user turns that clearly ask a question (explicit or strongly implied).
- Match each question with the immediate assistant response.
- Only include pairs where the assistant's reply directly addresses the user's question.
- Do not include incomplete, ambiguous, or out-of-context entries.

EXAMPLE:

Conversation:

user: Which food is best for diabetic patients?
assistant: Steel-cut oats are good for diabetic patients
user: Is it better if I eat muesli instead of oats?
assistant: While muesli is good for diabetic people, steel-cut oats are preferred. Refer to your nutritionist for better guidance.

Example JSON:
{
    "question": "Which food is best for diabetic patients?",
    "response": "Steel-cut oats are good for diabetic patients"
}
===== END OF EXAMPLE ======

**
IMPORTANT: Please make sure to only return in JSON format with one key: 'qa_pairs' and the value MUST be a list of dictionaries
**

Conversation:
{conversation}
JSON:
```

### Prompt B: `get_qa_pair_verdict`

```
You are given:
- A list of **relevant topics**
- A **user question**
- An **assistant response**

Your task is to:
1. Determine if the question is relevant to the list of topics.
2. If it is relevant, evaluate whether the response properly answers the question.
3. Based on both relevance and correctness, assign one of four possible verdicts.
4. Give a simple, comprehensive reason explaining why this question-answer pair was assigned this verdict

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

VERDICTS:
- `"TP"` (True Positive): Question is relevant and the response correctly answers it.
- `"FN"` (False Negative): Question is relevant, but the assistant refused to answer or gave an irrelevant response.
- `"FP"` (False Positive): Question is NOT relevant, but the assistant still gave an answer (based on general/training knowledge).
- `"TN"` (True Negative): Question is NOT relevant, and the assistant correctly refused to answer.

OUTPUT FORMAT:
Return only a **JSON object** with one key:
{
    "verdict": "TP"  // or TN, FP, FN
    "reason": "Reason why the verdict is 'TP'"
}

CHAIN OF THOUGHT:
- Check if the question aligns with any of the relevant topics.
- If yes:
    - Assess if the response is correct, complete, and directly answers the question.
- If no:
    - Check if the assistant refused appropriately or gave an unwarranted answer.
- Choose the correct verdict using the definitions above.

EXAMPLE:

Relevant topics: ["heath nutrition", "food and their benefits"]
Question: "Which food is best for diabetic patients?"
Response: "Steel-cut oats are good for diabetic patients"

Example JSON:
{
    "verdict": "TP",
    "reason": The question asks about food for diabetic patients and the response clearly answers that oats are good for diabetic patients. Both align with the relevant topics of heath nutrition and food and their benefits...
}

===== END OF EXAMPLE ======

**
IMPORTANT: Please make sure to only return in JSON format with two keys: 'verdict' and 'reason'
**

Relevant topics: {relevant_topics}
Question: {question}
Response: {response}

JSON:
```

### Prompt C: `generate_reason`

```
You are given a score for a metric that calculates whether an agent has adhered to it's topics.
You are also given a list of reasons for the truth table values that were used to calculate final score.

Your task is to go through these reasons and give a single final explaination that clearly explains why this metric has failed or passed.

**
IMPORTANT: Please make sure to only return in JSON format, with the 'reason' key providing the reason.
Example JSON:
{
    "reason": "The score is <score> because <your_reason>."
}

--- MULTIMODAL INPUT RULES ---
- Treat image content as factual evidence.
- Only reference visual details that are explicitly and clearly visible.
- Do not infer or guess objects, text, or details not visibly present.
- If an image is unclear or ambiguous, mark uncertainty explicitly.

Pass: {success}
Score: {score}
Threshold: {threshold}

Here are the reasons for all truth table entries:

True positive reasons: {TP[1]}
True negative reasons: {TN[1]}
False positives reasons: {FP[1]}
False negatives reasons: {FN[1]}

Score calculation = Number of True Positives + Number of True Negatives / Total number of table entries

**
IMPORTANT: Now generate a comprehensive reason that explains why this metric failed. You MUST output only the reason as a string and nothing else.
**

Output ONLY the reason, DON"T output anything else.

JSON:
```

**Schema:**
```python
class QAPair(BaseModel):
    question: str
    response: str

class QAPairs(BaseModel):
    qa_pairs: List[QAPair]

class RelevancyVerdict(BaseModel):
    verdict: Literal["TP", "TN", "FP", "FN"]
    reason: str

class TopicAdherenceReason(BaseModel):
    reason: str
```

---

## 8. ConversationalGEval

**Source:** `deepeval/metrics/conversational_g_eval/template.py`

### Prompt A: `generate_evaluation_steps`

```
Given an evaluation criteria which outlines how you should judge a conversation between a user and an LLM chatbot using the {parameters} fields in each turn, generate 3-4 concise evaluation steps based on the criteria below. Based on the evaluation criteria, you MUST make it clear how to evaluate the {parameters} in relation to one another in each turn, as well as the overall quality of the conversation.

Evaluation Criteria:
{criteria}

**
IMPORTANT: Please make sure to only return in JSON format, with the "steps" key as a list of strings. No words or explanation is needed.
Example JSON:
{
    "steps": <list_of_strings>
}
**

JSON:
```

### Prompt B: `generate_evaluation_results`

```
You are given a set of {dependencies} that describe how to assess a conversation between a user and an LLM chatbot. Your task is to return a JSON object with exactly two fields:

1. `"score"`: An integer from 0 to 10 (inclusive), where:
   - 10 = The conversation *fully* meets the criteria described in the Evaluation Steps
   - 0 = The conversation *completely fails* to meet the criteria
   - All other scores represent varying degrees of partial fulfillment,
   {score_explanation}.

2. `"reason"`: A **concise but precise** explanation for the score. {reasoning_guidance} and mention relevant details from the conversation and the given parameters. DO NOT include the score value in your explanation.

Evaluation Steps:
{evaluation_steps}

{rubric_text}Conversation:
{turns}

{test_case_content}

Parameters to consider during evaluation:
{parameters}

---
IMPORTANT: You MUST return only a valid JSON object with the exact keys `"score"` and `"reason"`. No additional text, commentary, or formatting.

---
Example JSON:
{
    "reason": "Your concise and informative reason here.",
    "score": 0
}

JSON:
```

**Schema:**
```python
class ReasonScore(BaseModel):
    reason: str
    score: float

class Steps(BaseModel):
    steps: List[str]
```
