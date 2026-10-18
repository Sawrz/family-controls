# Project workflow

Edit this repository's reusable implementation and tests. Deployment-specific
choices belong to nix-config. Do not modify live configuration or private runtime
knowledge to apply changes early. Use Forgejo for development and writes; GitHub
is a recovery mirror. Keep existing runtime state and identifiers intact. Build,
activation and runtime validation are separate outcomes.

# Python development

The following guidance is copied from `../cv/AGENTS.md`, with its Windows/WSL
shell instructions and dependency-specific "core package" wording omitted because
they do not apply to this repository. The project workflow above remains in force.

## Rules
- Never update this file autonomously; change it when the user requests it.
- Never make code changes in dependencies. Instead, explain what needs to change.

## Code quality
- Use Pydantic to ensure type safety wherever it makes sense.
- Prefer classes when they make sense; use functions when they are clearer.
- Never suppress code quality or security warnings. Fix them. If suppression is
  unavoidable, ask first.
- Write elegant, concise, readable code.

## Testing
- Use pytest for testing, including fixtures, parametrization and monkeypatching.
- Never use `unittest.mock`; use monkeypatch instead. If monkeypatch cannot be used
  and `unittest.mock` is needed, ask first.
- Test coverage should be at least 70%.
- Every test should cover a unique edge case and have a reason to exist. Avoid
  redundant tests.
- Never write tests for dependencies. Assume dependencies are well tested.
- Test only this repository's logic, never its Git submodules.

## Documentation
- Write concise, easy-to-understand documentation without fluff.
- Maintain a well-documented repository.
- Follow Google-style docstrings.
- Update documentation and docstrings when necessary.
