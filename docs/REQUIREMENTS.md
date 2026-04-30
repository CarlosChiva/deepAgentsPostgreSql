# Requirements

## Refactor `services/chat-api/` Application Structure

### Context
The current structure is in `services/chat-api/app/` with folders like `core/`, `schemas/`, `routers/`, `services/`, and `infrastructure/`.

### Instructions
1. **Read the FastAPI Skill**: Check `.agents/skills/fastapi/SKILL.md` carefully to understand the recommended project structure, best practices, and conventions for FastAPI.
2. **Refactor the Project Structure**: Refactor the files in `services/chat-api/app/` to align with the standard FastAPI conventions found in the skill (layered architecture, dependency injection patterns where appropriate, proper separation of concerns). Ensure the code logic remains functional.
3. **Review Middleware**: In `services/chat-api/app/main.py` (or the main entry point), review the current middleware setup (currently: CORS, `UserMiddleware`, request logging). Based on the FastAPI skill recommendations, ensure these are configured optimally or if you see the skill recommends specific middleware adjustments, apply them (e.g., security headers, better CORS config, etc.).
4. **Verify Dependencies**: Check `pyproject.toml` in `services/chat-api/` to ensure any necessary dependencies for the refactored structure are present.

### Files to Focus On
- `services/chat-api/app/main.py`
- `services/chat-api/app/` (all subfolders)
- `.agents/skills/fastapi/SKILL.md`

### Success Criteria
- Project structure aligns with FastAPI conventions from the skill
- Middleware configured optimally per FastAPI best practices
- All dependencies in pyproject.toml are correct for the refactored structure
- Code logic remains functional
