# Rules
- Never update this file autonomously!
- Never make any code changes in the dependencies, including the core package! Instead, tell the user what needs to be changed.

## Code Quality
- Use pydantic to ensure type safety wherever it makes sense.
- We prefer class structures when it makes sense. We don't enforce them mindlessly, functions can sometimes be better, but in most cases class structures are the way to go.
- Never suppress code quality or security warnings. Instead fix them. If you have to suppress them because there is no other way ask first.
- Write elegant, concise yet readable code.

## Testing
- Use pytest for testing, including fixtures, parametrization and monkeypatching.
- Never use `unittest.mock`,use monkeypatch instead. If monkeypatch can't be used and you need to use `unittest.mock` ask first.
- The test coverage should be at least 70%
- Every test should cover a unique edge case and needs to have reason to exist. Redundant tests are unnecessary!
- Never write tests for dependencies. Assume the dependencies are well-tested.
- Only test the logic of this repository but never of its git submodules.

## Documentation
- Write concise, yet easy to understand documentation.
- No fluff. You wire straight to the point documentation.
- Maintain a well documented repository.
- Docstrings follow the Google-style format.
- Update documentation or docstrings when necessary.

## System Specifics
- If the repo path contains WSL markers such as `\\wsl$\`, `/home/`, `/mnt/`, or `:\home\` on a Windows drive like `W:\home\...`, run shell commands via `wsl`; otherwise use native PowerShell.