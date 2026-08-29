# Claude Compatibility

Follow the repository policy in `AGENTS.md`.

GitNexus is installed globally. Keep its repository index in the ignored
`.gitnexus/` directory and refresh it without generated instruction or skill
copies:

```powershell
gitnexus analyze --index-only .
```

Use the globally installed `gitnexus-*` skills for exploration, impact
analysis, review, refactoring, debugging, and CLI maintenance.
