---
name: GitHub connector push behavior
description: How authenticated GitHub publishing behaves when command-line Git cannot use the connector.
---

The Replit GitHub connector authenticates GitHub API requests but does not necessarily authenticate HTTPS command-line Git pushes. If GitHub's low-level blob endpoint is blocked by the connector proxy, use the GraphQL `createCommitOnBranch` mutation to publish the complete local tree atomically.

**Why:** An attached, healthy GitHub connector left `git push` unauthenticated, and the REST blob endpoint returned a Cloudflare 403. The GraphQL atomic commit accepted the same repository state and produced a remote tree hash identical to the local tree.

**How to apply:** Try the normal push once. If HTTPS authentication fails, prefer Git database API writes when available; otherwise create one atomic GraphQL commit with all tracked files and deletions. Verify local and remote tree hashes match before aligning the local branch.