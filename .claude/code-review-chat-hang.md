# Code Review: Chat Page Hanging Issue

**Reviewed**: 2026-04-21
**Issue**: Chat page at http://127.0.0.1:9119/chat hangs when executing script commands
**Scope**: Backend streaming architecture and async/threading patterns

---

## Summary

The chat page hangs when executing script commands due to **blocking async operations** in the streaming architecture. The root cause is mixing async/await patterns with blocking thread operations, which blocks the event loop during long-running script execution.

---

## Critical Issues

### 🔴 CRITICAL #1: Blocking Event Loop in Async Function

**File**: `hermes_cli/web_chat_api/chat_stream.py:214-238`

**Issue**: The `run_chat_stream()` async function uses blocking `thread.join()` calls, which blocks the asyncio event loop.

```python
# Current problematic code
async def run_chat_stream(...):
    # ... setup code ...
    
    def _run_agent():
        result = agent.run_conversation(user_message)
        result_queue.put(('success', result))
    
    agent_thread = threading.Thread(target=_run_agent, daemon=True)
    agent_thread.start()
    
    # ❌ BLOCKS THE EVENT LOOP
    while agent_thread.is_alive():
        if cancel_event.is_set():
            agent.interrupt("Cancelled by user")
            agent_thread.join(timeout=5)  # Blocks for up to 5 seconds
            return
        agent_thread.join(timeout=0.1)  # Blocks for 100ms each iteration
```

**Impact**: 
- When script commands run (which can take seconds or minutes), the event loop is blocked
- Frontend SSE connection appears frozen
- No other async operations can proceed
- User sees page hang with no feedback

**Severity**: CRITICAL - Must fix before merge

**Recommended Fix**:
```python
async def run_chat_stream(...):
    # Use asyncio.to_thread() for proper async-to-sync bridging
    loop = asyncio.get_event_loop()
    
    def _run_agent():
        result = agent.run_conversation(user_message)
        return result
    
    try:
        # Run in thread pool without blocking event loop
        result = await loop.run_in_executor(None, _run_agent)
        
        # Handle result
        final_response = result.get('final_response', '')
        if on_complete:
            on_complete(final_response, session.messages)
            
    except Exception as e:
        if on_error:
            on_error(str(e))
```

---

### 🔴 CRITICAL #2: Nested Event Loops

**File**: `hermes_cli/web_server.py:2449-2466`

**Issue**: Running `asyncio.run()` inside an executor creates nested event loops, causing unpredictable behavior.

```python
# Current problematic code in chat_stream_post()
with concurrent.futures.ThreadPoolExecutor() as executor:
    future = loop.run_in_executor(
        executor,
        lambda: asyncio.run(run_chat_stream(...))  # ❌ Creates new event loop in thread
    )
    
    while not future.done():
        while queue_data:
            event_type, data = queue_data.pop(0)
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        await asyncio.sleep(0.05)
```

**Impact**:
- Creates complex threading situation with multiple event loops
- The inner `run_chat_stream()` blocks waiting for agent thread
- Outer loop polls for completion
- Race conditions and deadlocks possible

**Severity**: CRITICAL - Must fix before merge

**Recommended Fix**:
```python
# Use the existing event loop, don't create a new one
async def event_generator():
    queue_data = []
    
    # ... callback definitions ...
    
    # Run directly in current event loop
    try:
        await run_chat_stream(
            session_id=session_id,
            user_message=body.message,
            model=session["model"],
            workspace=session["workspace"],
            on_token=on_token,
            on_tool=on_tool,
            on_complete=on_complete,
            on_error=on_error,
        )
    except Exception as e:
        queue_data.append(("error", {"message": str(e)}))
    
    # Yield all collected events
    for event_type, data in queue_data:
        yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
```

---

## High Priority Issues

### 🟠 HIGH #1: Synchronous Callbacks in Async Context

**File**: `hermes_cli/web_chat_api/chat_stream.py:175-186`

**Issue**: Callbacks are synchronous but called from async context, limiting responsiveness.

```python
def _on_token_callback(delta, **kwargs):
    if cancel_event.is_set():
        return
    collected_tokens.append(delta)
    if on_token:
        on_token(delta)  # Synchronous callback
```

**Impact**: Token streaming may be delayed during script execution

**Severity**: HIGH - Should fix before merge

**Recommended Fix**: Make callbacks async-aware or use thread-safe queues

---

### 🟠 HIGH #2: No Timeout for Agent Execution

**File**: `hermes_cli/web_chat_api/chat_stream.py:228-238`

**Issue**: Agent execution has no overall timeout, can hang indefinitely.

```python
while agent_thread.is_alive():
    # No timeout check here
    agent_thread.join(timeout=0.1)
```

**Impact**: Runaway scripts can hang the chat indefinitely

**Severity**: HIGH - Should fix before merge

**Recommended Fix**: Add configurable timeout (e.g., 5 minutes) with proper error handling

---

## Medium Priority Issues

### 🟡 MEDIUM #1: Inefficient Polling Loop

**File**: `hermes_cli/web_chat_api/chat_stream.py:228`

**Issue**: Polling with 100ms sleep is inefficient and still blocks.

```python
agent_thread.join(timeout=0.1)  # Blocks for 100ms
```

**Impact**: Adds latency to cancellation and completion detection

**Severity**: MEDIUM - Fix recommended

**Recommended Fix**: Use async/await patterns instead of polling

---

### 🟡 MEDIUM #2: Queue Timeout in SSE Stream

**File**: `hermes_cli/web_server.py:2192`

**Issue**: 30-second timeout may be too short for long-running scripts.

```python
event = await asyncio.wait_for(queue.get(), timeout=30.0)
```

**Impact**: Connection may timeout during legitimate long operations

**Severity**: MEDIUM - Fix recommended

**Recommended Fix**: Increase timeout or send periodic progress events

---

## Architecture Analysis

### Current Flow (Problematic)

```
Frontend (messages.js)
  ↓ POST /api/chat/start
Backend (web_server.py:chat_start)
  ↓ Creates queue + background task
  ↓ asyncio.create_task(run_background())
chat_stream.py:run_chat_stream() [ASYNC]
  ↓ Creates thread
  ↓ thread.start()
  ↓ while thread.is_alive(): thread.join(0.1)  ← BLOCKS EVENT LOOP
Agent Thread (SYNC)
  ↓ agent.run_conversation()
  ↓ Executes bash tools (long-running)
  ↓ Blocks until complete
```

**Problem**: The async function blocks waiting for the sync thread, blocking the event loop.

### Recommended Flow

```
Frontend (messages.js)
  ↓ POST /api/chat/start
Backend (web_server.py:chat_start)
  ↓ Creates queue + background task
  ↓ asyncio.create_task(run_background())
chat_stream.py:run_chat_stream() [ASYNC]
  ↓ await loop.run_in_executor(None, agent.run_conversation)
  ↓ Event loop remains free
Agent Thread Pool (SYNC)
  ↓ agent.run_conversation()
  ↓ Executes bash tools
  ↓ Callbacks fire → queue.put_nowait()
Event Loop
  ↓ Continues processing other requests
  ↓ SSE stream reads from queue
  ↓ Frontend receives events in real-time
```

**Benefit**: Event loop never blocks, frontend stays responsive.

---

## Root Cause Summary

1. **Async/Sync Mixing**: Async function uses blocking thread operations
2. **Event Loop Blocking**: `thread.join()` blocks the event loop during script execution
3. **Nested Event Loops**: `asyncio.run()` inside executor creates complexity
4. **No Proper Async Bridging**: Missing `asyncio.to_thread()` or proper executor usage

---

## Validation Results

| Check | Result |
|-------|--------|
| Type check | Skipped (no TypeScript) |
| Lint | Not run |
| Tests | Not run |
| Manual testing | Issue confirmed - page hangs during script execution |

---

## Files Reviewed

- `hermes_cli/web_server.py` (lines 2171-2508) - SSE streaming endpoints
- `hermes_cli/web_chat_api/chat_stream.py` (full file) - Chat stream implementation
- `hermes_cli/web_chat_dist/messages.js` (lines 1-100) - Frontend chat logic

---

## Recommended Action Plan

1. **Immediate**: Fix CRITICAL #1 - Remove blocking `thread.join()` calls
2. **Immediate**: Fix CRITICAL #2 - Remove nested `asyncio.run()`
3. **Short-term**: Add timeout for agent execution (HIGH #2)
4. **Short-term**: Make callbacks async-aware (HIGH #1)
5. **Long-term**: Refactor to fully async architecture

---

## Decision

**BLOCK** - Critical issues must be fixed before merge.

The current implementation has fundamental async/threading issues that cause the chat page to hang during script execution. These must be resolved to provide a functional user experience.

---

## Next Steps

1. Refactor `run_chat_stream()` to use `loop.run_in_executor()` properly
2. Remove nested event loop creation in `chat_stream_post()`
3. Add comprehensive timeout handling
4. Test with long-running script commands
5. Verify frontend remains responsive during execution
