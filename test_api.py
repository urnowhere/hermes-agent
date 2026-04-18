from hermes_cli.model_switch import list_authenticated_providers
import json
print(json.dumps(list_authenticated_providers(), indent=2))
