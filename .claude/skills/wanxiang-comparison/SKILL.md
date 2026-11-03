---
name: wanxiang-comparison
description: Use when the user wants several objects compared on shared dimensions with traceable evidence. This entry point is not implemented in v1 and must redirect to /wanxiang-deep.
---

# Wanxiang Comparison (not implemented)

**Status: not implemented in v1.** Do not pretend to run this mode.

When this entry point is invoked:

1. Tell the user, in their language, that Comparison mode is not implemented
   in v1.
2. Do not discover sources, call the compliance gate, collect pages, create a
   research project or an archive, write a comparison record, or write any
   file.
3. Offer to run `/wanxiang-deep` instead. Deep Research is the only fully
   implemented mode; it can research each object in a separate project, but it
   does not produce the structured comparison artifacts planned for this mode.

This file is a mode entry point only. The engine and protocol live in the
repository root `SKILL.md`, which is the `/wanxiang-deep` entry point.
