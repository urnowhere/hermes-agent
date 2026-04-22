# OpenAI Tool Call Format

When calling tools, your response must use the `tool_calls` field in the assistant message. Do NOT put tool calls in the text content.

## Correct Format

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "tool_name",
        "arguments": "{\"arg1\": \"value1\", \"arg2\": 42}"
      }
    }
  ]
}
```

## Rules

1. `arguments` must be a JSON **string** (not an object) — serialize it with `json.dumps`.
2. Only include arguments defined in the tool's schema. Do not add extra arguments.
3. Required arguments must always be present.
4. Argument types must match the schema exactly (string, integer, boolean, array, object).
5. To call multiple tools, include multiple entries in the `tool_calls` array.

## Common Mistakes

- ❌ Putting tool call JSON in `content` as text
- ❌ Adding `arguments` as a dict instead of a JSON string
- ❌ Inventing argument names not in the schema
- ❌ Omitting required arguments
