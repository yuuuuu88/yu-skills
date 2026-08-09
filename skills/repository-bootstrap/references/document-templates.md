# Document Templates

## Common frontmatter

~~~yaml
---
id: DOC-001
type: design
title: Document title
status: draft
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: []
managed_by: repository-bootstrap
---
~~~

statusは文書状態であり、実装進捗ではない。

## Folder README

すべての初期生成フォルダに責務と正本を説明するREADMEを置く。

~~~markdown
---
id: INDEX-001
type: documentation-index
title: Folder index
status: accepted
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: []
managed_by: repository-bootstrap
---

# Folder index

## Responsibility

このフォルダに置く文書の責務。

## Documents

- filename.md: purpose

## Rules

このフォルダ固有の配置ルール。
~~~

## Product brief

~~~markdown
---
id: REQ-001
type: requirements
title: Product brief
status: draft
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: []
managed_by: repository-bootstrap
---

# Product brief

## Problem
## Users and stakeholders
## Goals
## Non-goals
## Functional requirements
## Quality requirements
## Constraints and assumptions
## Acceptance criteria
## Open questions
## References
~~~

## Architecture overview

~~~markdown
---
id: ARCH-001
type: architecture
title: System architecture overview
status: draft
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: []
managed_by: repository-bootstrap
---

# System architecture overview

## Scope and context
## Quality goals
## Constraints
## Solution strategy
## System context
## Containers / major building blocks
## Runtime scenarios
## Deployment view
## Cross-cutting concerns
## Risks and technical debt
## Glossary
## Related documents
~~~

## Feature design

~~~markdown
---
id: DES-001
type: design
title: Initial implementation slice
status: draft
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: [REQ-001, ARCH-001]
managed_by: repository-bootstrap
---

# Initial implementation slice

## Scope
## Related requirements
## Assumptions
## User or system flow
## Inputs and outputs
## Interfaces and data
## Validation and error handling
## Authentication and authorization
## Security and privacy
## Observability
## Test strategy
## Alternatives and trade-offs
## Open questions
## References
~~~

## ADR

ADRはdesign/decisions/に置き、1ファイル1意思決定にする。accepted後の変更は後続ADRで置き換える。

~~~markdown
---
id: ADR-0001
type: adr
title: Decision title
status: proposed
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: []
managed_by: repository-bootstrap
---

# ADR-0001: Decision title

## Context
## Decision
## Alternatives considered
## Consequences
## Revisit trigger
## References
~~~

## Plan

進捗はplansだけに置く。

~~~markdown
---
id: PLAN-001
type: plan
title: Bootstrap implementation plan
status: active
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: [REQ-001, ARCH-001, DES-001]
managed_by: repository-bootstrap
---

# Bootstrap implementation plan

## Objective
## Ordered tasks
- [ ] Complete product brief
- [ ] Complete architecture
- [ ] Complete initial feature design
- [ ] Validate documents
- [ ] Start implementation
## Validation commands
## Checkpoints
## Blockers
## Decision links
~~~
