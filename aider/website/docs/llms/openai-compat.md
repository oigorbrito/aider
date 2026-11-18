---
parent: Connecting to LLMs
nav_order: 500
---

# OpenAI compatible APIs

Aider can connect to any LLM which is accessible via an OpenAI compatible API endpoint.

The endpoint must be a cloud service. aider refuses an endpoint that points to this machine (a loopback, private or link-local address, or `localhost`), in `--openai-api-base`, `OPENAI_API_BASE`, other `*_API_BASE` variables and model settings.

First, install aider:

{% include install.md %}

Then configure your API key and endpoint:

```
# Mac/Linux:
export OPENAI_API_BASE=<endpoint>
export OPENAI_API_KEY=<key>

# Windows:
setx OPENAI_API_BASE <endpoint>
setx OPENAI_API_KEY <key>
# ... restart shell after setx commands
```

Start working with aider and your OpenAI compatible API on your codebase:

```bash
# Change directory into your codebase
cd /to/your/project

# Prefix the model name with openai/
aider --model openai/<model-name>
```

See the [model warnings](warnings.html)
section for information on warnings which will occur
when working with models that aider is not familiar with.
