# CLAUDE.md

## Behavioral Guidelines (Karpathy)

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Commands
<!-- 기술 스택 확정 후 채울 것 -->
- Build: `<TBD>`
- Test: `<TBD>` (watch 모드 금지)
- Lint: `<TBD>`
- Typecheck: `<TBD>`

## Project-Specific Gotchas
<!-- 자동 reflection으로 누적됨. 초기에는 비워두기 -->

## Measurable Conventions
<!-- 측정 가능한 것만. "잘 짜라" 같은 추상 표현 금지 -->

## Self-Reflection on Errors
When an error, exception, test failure, or unexpected behavior occurs
during this session, perform reflection AUTONOMOUSLY — do not wait for
the user to point it out:

1. STOP. Do not patch the symptom or suppress the error.
2. Analyze the root cause:
   - What was the actual failure mode (not just the error message)?
   - Why did this happen? Trace back to the originating decision or
     assumption that led here.
   - Was this caused by a silent assumption, missing context, or
     ignored convention?
   - Is this an instance of a pattern that could recur?
3. Fix the root cause, not the symptom.
4. After fixing, ask: "Would a rule in this CLAUDE.md have prevented
   this error?"
   - If YES → propose adding the rule (one line, specific, measurable)
     to the relevant section (Gotchas / Conventions). Show the proposed
     change and wait for user confirmation before writing.
   - If NO → log the lesson to Auto Memory instead, since it's a
     transient environmental issue rather than a project rule.

The goal is preventing the same CLASS of error from recurring. Every
error is a free lesson — capture it before it escapes.

## Project Context
AI Agent들을 구축하고, 그 Agent들이 외부 도구·API에 접근할 때 거치는
**MCP Gateway**를 만드는 프로젝트. Gateway는 여러 MCP 서버를 단일
진입점으로 묶어 라우팅·인증·관측을 담당하는 것이 목표.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
