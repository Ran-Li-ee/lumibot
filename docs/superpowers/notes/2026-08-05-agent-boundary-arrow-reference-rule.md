# Agent Boundary Arrow Reference Rule

This note records the short reference notation used in discussions about the
agent workflow replay trace. It is intended to survive context compaction and
make future conversations precise.

## Agent IDs

```text
A1 = Growth Agent
A2 = Decision Agent
A3 = Execution Agent
```

## Round IDs

```text
R1, R2, R3...
```

A round means one model turn inside one agent. In plain language, one round is
one cycle where Google ADK sends context to the model, the model responds, and
the system either executes requested tools or receives a final text response.

Example:

```text
A1R2 = Growth Agent, second model turn.
```

## Model Request Arrows

These arrows identify the model-facing path inside a round.

```text
1 = Google ADK -> LiteLLM
2 = LiteLLM -> OpenAI LLM
3 = OpenAI LLM -> LiteLLM
4 = LiteLLM -> Google ADK
```

Example:

```text
A1R2-1 = Growth Agent, round 2, Google ADK sends the model request to LiteLLM.
```

## Local Tool Call Arrows

If the model asks to call local tools, the tool execution path is:

```text
5  = Google ADK -> ADK FunctionTool
6  = ADK FunctionTool -> Lumibot Tool Package Layer
7  = Lumibot Tool Package Layer -> Local Python Tool Function
8  = Local Python Tool Function -> Lumibot Tool Package Layer
9  = Lumibot Tool Package Layer -> ADK FunctionTool
10 = ADK FunctionTool -> Google ADK
```

## Post-Tool Model Request Arrows

After all local tool calls in a round have returned to Google ADK, Google ADK
may send the accumulated tool results back to the model. In teaching diagrams,
this return-to-model path is labeled as arrows 11-14:

```text
11 = Google ADK -> LiteLLM
12 = LiteLLM -> OpenAI LLM
13 = OpenAI LLM -> LiteLLM
14 = LiteLLM -> Google ADK
```

Plain meaning:

```text
11-14 describe the next model-facing request after one or more tool results
have returned to Google ADK. The model may then request more tools or produce a
final summary.
```

## Multiple Tool Calls In One Round

When one round contains multiple tool calls, add a decimal suffix.

```text
5.1, 6.1, 7.1, 8.1, 9.1, 10.1
5.2, 6.2, 7.2, 8.2, 9.2, 10.2
5.3, 6.3, 7.3, 8.3, 9.3, 10.3
...
```

Example:

```text
A1R2-5.3
```

Means:

```text
Growth Agent
Round 2
Third local tool call in that round
Google ADK -> ADK FunctionTool
```

Another example:

```text
A3R2-7.1
```

Means:

```text
Execution Agent
Round 2
First local tool call in that round
Lumibot Tool Package Layer -> Local Python Tool Function
```

## 11-14 Notation Caveat

For a single-loop teaching diagram, arrows 11-14 describe the next model
request after all tool results have returned to Google ADK:

```text
11 = Google ADK -> LiteLLM
12 = LiteLLM -> OpenAI LLM
13 = OpenAI LLM -> LiteLLM
14 = LiteLLM -> Google ADK
```

For the full workflow diagram, prefer round-based notation instead:

```text
A1R2-1
A1R2-2
A1R2-3
A1R2-4
```

rather than referring back to the prior round as 11-14. This avoids ambiguity
when many agents and rounds are shown together.
