# sampling-command plugin

CLI-only `/sampling` slash command for live session sampling control.

## Install (from repo checkout)

```bash
mkdir -p ~/.hermes/plugins/sampling-command
cp optional-plugins/sampling-command/plugin.yaml ~/.hermes/plugins/sampling-command/
cp optional-plugins/sampling-command/__init__.py ~/.hermes/plugins/sampling-command/
```

Restart Hermes, then use:

- `/sampling`
- `/sampling 0.7 0.95`
- `/sampling temperature 0.4`
- `/sampling top_p 0.9`
- `/sampling reset`
- `/sampling save`
