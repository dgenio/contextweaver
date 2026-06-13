---
name: Good first issue
description: Starter-sized issue with clear scope and acceptance criteria
title: "[Starter]: "
labels: ["good first issue", "help wanted", "agent-friendly", "complexity/good-first-issue"]
body:
  - type: markdown
    attributes:
      value: |
        Use this template for issues that are safe for new contributors and coding agents.
  - type: input
    id: area
    attributes:
      label: Area label
      description: Choose one primary area label, for example area/docs or area/adapters.
      placeholder: area/docs
    validations:
      required: true
  - type: input
    id: type
    attributes:
      label: Type label
      description: Choose a type label, for example documentation, testing, integration, or developer-experience.
      placeholder: documentation
    validations:
      required: true
  - type: textarea
    id: problem
    attributes:
      label: Problem
      description: What small, concrete problem should be solved?
    validations:
      required: true
  - type: textarea
    id: scope
    attributes:
      label: Scope
      description: Name the one file/module/page, or the small set of files, expected to change.
      placeholder: "Expected files: docs/example.md"
    validations:
      required: true
  - type: textarea
    id: acceptance
    attributes:
      label: Acceptance criteria
      description: List testable completion criteria.
      placeholder: |
        - [ ] ...
        - [ ] ...
        - [ ] ...
    validations:
      required: true
  - type: textarea
    id: tests
    attributes:
      label: Suggested tests or checks
      description: Commands or manual checks a contributor should run.
      placeholder: "python -m pytest tests/..."
    validations:
      required: false
  - type: checkboxes
    id: starter_checks
    attributes:
      label: Starter issue checklist
      options:
        - label: Does not require secrets, credentials, or live services
          required: true
        - label: Does not require deep architecture decisions
          required: true
        - label: Has explicit acceptance criteria
          required: true
