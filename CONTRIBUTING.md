# Contributing to GitDeploy

## Adding a New Language/Framework Handler

1. **Stack detection** — Add file indicators to `backend/analyzer/stack_detector.py`:
   ```python
   INDICATORS = {
       'your-stack': ['your-config-file.toml'],
   }
   ```

2. **Dockerfile template** — Add a template generator in `backend/ai/dockerfile_templates.py` → `TEMPLATE_MAP`.

3. **Config file parsing** — Add parsing logic in `backend/analyzer/repo_analyzer.py` → `_parse_config_files()`.

4. **Sandbox Dockerfile** — Create `docker/sandbox/Dockerfile.yourlang` with the runtime, package manager, git, curl, and a `sandbox` user.

5. **Image mapping** — Register in `backend/runner/docker_runner.py`:
   ```python
   STACK_TO_IMAGE = {
       'your-stack': 'gitdeploy-yourlang:latest',
   }
   ```

6. **Build command** — Add to `Makefile` → `build-sandbox-images`.

## Adding a New Database Type

1. **Static signals** — Add detection patterns to `backend/analyzer/db_detector.py` → `DB_SIGNALS`.

2. **Image & port** — Add to `backend/runner/db_provisioner.py`:
   ```python
   DB_IMAGES = {'yourdb': 'yourdb:latest'}
   DB_PORTS  = {'yourdb': 12345}
   ```

3. **Health check** — Add to `DB_HEALTH_CMDS` in `db_provisioner.py`.

4. **Container env** — Add init env vars in `backend/runner/credential_manager.py` → `build_container_env()`.

5. **App env map** — Add connection string patterns in `credential_manager.py` → `build_env_map()`.

6. **DB type enum** — Add to `backend/db/models.py` → `DBType` enum, then create an Alembic migration.

## Code Style

- Python: type hints everywhere, async where appropriate
- Format with `black`, lint with `ruff`
- Frontend: functional React components, Tailwind CSS

## Testing

```bash
make test
```

## Pull Requests

- One feature per PR
- Include tests for new handlers
- Update README if adding user-facing features
