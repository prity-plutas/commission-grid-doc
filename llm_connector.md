# llm_connector

A provider-agnostic Python connector for LLMs, driven entirely by environment
variables and built around the **Abstract Factory** pattern. Switch between
OpenAI, Ollama, HuggingFace, Anthropic (Claude), NVIDIA NIM, Groq, Cohere, or
LangChain-wrapped models by changing env vars only -- zero code changes.

## Why this design

- **One env-var contract, many backends.** Generic settings (`LLM_MODEL`,
  `LLM_MODE`, `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`, `LLM_OUTPUT_FORMAT`,
  `LLM_TIMEOUT`, ...) apply no matter which provider is active. Credentials
  are namespaced per-provider (`OPENAI_API_KEY`, `OLLAMA_BASE_URL`, ...) so
  multiple providers' secrets can coexist in one `.env`.
- **Abstract Factory.** `LLMProviderFactory.create(config)` looks up
  `config.provider` in a registry and instantiates the matching
  `BaseLLMProvider` subclass. Adding a new backend = one new provider class
  + one registry entry.
- **Everything library-specific stays inside that library's class.** Auth,
  request shape, response parsing, and error types unique to (say) the
  `openai` SDK live only in `OpenAIProvider`. Nothing else in the codebase
  needs to know `response.choices[0].message.content` is OpenAI's shape.
- **Normalized in, normalized out.** Every provider receives the same
  `LLMRequest` and returns the same `LLMResponse`, regardless of backend.
- **Generic prompt building.** `PromptBuilder` composes system prompt,
  history, retrieved context, and the user's question into either a chat
  message list or a flattened text prompt, independent of provider.

## Project layout

```
llm_connector/
  config.py            # env var loading, validation, ProviderType/Mode/OutputFormat enums
  exceptions.py         # normalized exception hierarchy
  base.py               # BaseLLMProvider (abstract product), LLMRequest, LLMResponse
  prompt_builder.py      # generic, mode-agnostic prompt construction
  factory.py             # LLMProviderFactory (Abstract Factory)
  client.py              # LLMClient -- the public-facing facade
  providers/
    openai_provider.py
    ollama_provider.py
    huggingface_provider.py
    anthropic_provider.py
    nvidia_provider.py
    groq_provider.py
    cohere_provider.py
    langchain_provider.py   # meta-provider; wraps another provider via LangChain
tests/
  test_llm_connector.py  # unit tests using a MockProvider (no network needed)
.env.example             # every supported env var, documented
requirements.txt         # per-provider optional dependencies
example_usage.py
```

## Setup

```bash
cp .env.example .env
# edit .env: set LLM_PROVIDER, LLM_MODEL, and that provider's credentials
pip install -r requirements.txt   # or just the one SDK you need, see below
```

Each provider only needs its own SDK installed -- you don't need every
package in `requirements.txt`, just the one matching `LLM_PROVIDER`:

| LLM_PROVIDER  | Install                          | Notes |
|---------------|-----------------------------------|-------|
| `openai`      | `pip install openai`              | |
| `ollama`      | `pip install ollama`              | free, local, no API key |
| `huggingface` (alias `hf`) | `pip install huggingface_hub` | free tier available |
| `anthropic` (alias `claude`) | `pip install anthropic` | |
| `nvidia` (alias `nim`) | `pip install openai` | NIM is OpenAI-API-compatible |
| `groq`        | `pip install groq`                | free tier, very fast |
| `cohere`      | `pip install cohere`              | free trial tier |
| `langchain`   | `pip install langchain-core` + the integration matching `LANGCHAIN_PROVIDER` | meta-provider |

## Usage

```python
from llm_connector import LLMClient, OutputFormat

client = LLMClient.from_env()          # reads LLM_PROVIDER, LLM_MODEL, etc.
response = client.ask("What's the capital of France?")
print(response.text)
print(response.provider, response.model, response.total_tokens)
```

Multi-turn + retrieved context:

```python
response = client.ask(
    "What's my favorite color?",
    history=[
        {"role": "user", "content": "My favorite color is blue."},
        {"role": "assistant", "content": "Got it!"},
    ],
)
```

Full control via `PromptBuilder`, plus structured JSON output:

```python
request = (
    client.prompt_builder()
    .with_system("You are a precise data extraction API.")
    .with_user("List 3 planets and one fact about each.")
    .with_output_format(OutputFormat.JSON)
    .with_generation_params(max_tokens=300, temperature=0.2)
    .build()
)
response = client.complete(request)
print(response.parsed_json)
```

Per-call overrides without touching the environment or shared config:

```python
fast_client_cfg = client.config.with_overrides(max_tokens=64, temperature=0.0)
fast_client = LLMClient(fast_client_cfg)
```

## Error handling

All providers raise from one hierarchy (`llm_connector.exceptions`), so
calling code never needs to import/catch provider-specific SDK exceptions:

```python
from llm_connector.exceptions import (
    ConfigurationError,        # bad/missing env vars
    ProviderInitializationError,  # SDK not installed, bad auth, unreachable host
    LLMRequestError,           # the call itself failed
    LLMTimeoutError,           # call exceeded LLM_TIMEOUT
    ResponseParsingError,      # output_format=json but model didn't return valid JSON
)
```

## Extending with a new provider

1. Add the new name to `ProviderType` in `config.py`, plus its env-var
   block in `_load_provider_env`.
2. Create `providers/my_provider.py` with a class extending
   `BaseLLMProvider`, implementing `_setup_client()` and `_call()`.
3. Register it in `factory.py`'s `_REGISTRY` (or call
   `LLMProviderFactory.register(...)` at runtime without touching the file).

No other file needs to change.

## Testing

```bash
python -m unittest tests.test_llm_connector -v
```

Tests use a `MockProvider` registered through the factory's public
extension hook, so the full config -> factory -> client -> provider ->
normalized response pipeline is exercised without any network calls or
real API keys.
