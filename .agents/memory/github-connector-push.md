---
name: GitHub connector push behavior
description: How authenticated GitHub publishing behaves when command-line Git cannot use the connector.
---

The Replit GitHub connector authenticates GitHub API requests but does not necessarily authenticate HTTPS command-line Git pushes.

**Why:** An attached, healthy GitHub connector still left `git push` unauthenticated, while Git database API writes through the connector succeeded.

**How to apply:** Try the normal push once. If HTTPS authentication fails, use the authenticated Git database API to create blobs, trees, commits, and update the branch ref; verify the published tree matches the local tree before aligning the local branch.