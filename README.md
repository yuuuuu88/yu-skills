# yu-skills

Reusable skills and safe local orchestration tools for AI-assisted software
development.

## Contents

- [agent-nous](./agent-nous/README.md): durable `plan → implement → review`
  task orchestration with explicit policy and evidence boundaries
- [nous-pilot](./nous-pilot/README.md): a development control plane for
  research, planning, implementation, testing, review, and repair
- [repository-bootstrap](./skills/repository-bootstrap/SKILL.md): initialize
  a readable, design-first repository structure
- [design-docs](./skills/design-docs/SKILL.md): create traceable design
  documents, contracts, and requirement links
- [clear-japanese-docs](./skills/clear-japanese-docs/SKILL.md): rewrite
  Japanese documentation into a clear reader flow
- [takt-operations](./skills/takt-operations/SKILL.md): configure and
  troubleshoot TAKT workflows safely
- [architecture-diagram-pptx](./skills/architecture-diagram-pptx/SKILL.md):
  create editable PowerPoint architecture and network diagrams

## Safety boundary

This repository contains source code, reusable skills, examples, tests, and
design guidance. It does not contain credentials, environment files, private
runtime state, local task data, review records, or generated validation output.

Remote Git operations, deployment, paid services, and live provider access are
not enabled by default in the included tools.

## License

Original work in this repository is released under the [MIT License](./LICENSE).
Third-party notices remain in their respective asset directories.
