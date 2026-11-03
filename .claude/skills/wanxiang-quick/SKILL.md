---
name: wanxiang-quick
description: Use when the user wants a quick, narrowly scoped answer with authoritative citations. This entry point is not implemented in v1 and must redirect to /wanxiang-deep.
---

# Wanxiang Quick (not implemented)

**Status: not implemented in v1.** Do not pretend to run this mode.

When this entry point is invoked:

1. Tell the user, in their language, that Quick mode is not implemented in v1.
2. Do not discover sources, call the compliance gate, collect pages, create a
   research project or an archive, or write any file.
3. Offer to run `/wanxiang-deep` instead. Deep Research is the only fully
   implemented mode, and it can answer a narrow question with a smaller scope.

This file is a mode entry point only. The engine and protocol live in the
repository root `SKILL.md`, which is the `/wanxiang-deep` entry point.
