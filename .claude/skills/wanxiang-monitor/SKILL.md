---
name: wanxiang-monitor
description: Use when the user wants to track a topic over time and be told what changed. This entry point is not implemented in v1 and must redirect to /wanxiang-deep.
---

# Wanxiang Monitor (not implemented)

**Status: not implemented in v1.** Do not pretend to run this mode.

When this entry point is invoked:

1. Tell the user, in their language, that Monitoring mode is not implemented
   in v1.
2. Do not discover sources, call the compliance gate, collect pages, create a
   research project or an archive, take tracking snapshots, or write any file.
3. Offer to run `/wanxiang-deep` instead. Deep Research is the only fully
   implemented mode; it produces a one-time research archive, not incremental
   tracking snapshots or change summaries.

This file is a mode entry point only. The engine and protocol live in the
repository root `SKILL.md`, which is the `/wanxiang-deep` entry point.
