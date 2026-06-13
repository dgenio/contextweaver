# Adapter PR Checklist

Copy this checklist into adapter PR descriptions.

## Scope

- [ ] Adapter is limited to provider/framework translation.
- [ ] No live network calls are made by adapter conversion functions.
- [ ] No credentials, tokens, or provider secrets are required for tests.
- [ ] Public functions return contextweaver-native types or plain Python data.

## Optional dependencies

- [ ] Provider imports are guarded or delayed until needed.
- [ ] Importing `contextweaver.adapters.<name>` works without provider credentials.
- [ ] Import errors mention the provider package or optional extra to install.
- [ ] Optional dependency naming is documented if new packaging wiring is needed.

## API shape

- [ ] Tool conversion function is named `from_<name>_tools` when tools are supported.
- [ ] History/message conversion function is named `from_<name>_history` when history is supported.
- [ ] Unsupported provider inputs raise clear `TypeError` or `ValueError` messages.
- [ ] Provider-specific objects do not leak into contextweaver core types.

## Tests

- [ ] Tests use local fake provider objects or mappings.
- [ ] Tests cover at least one successful tool conversion.
- [ ] Tests cover history/message conversion if implemented.
- [ ] Tests cover missing or invalid input.
- [ ] Tests can run offline with no provider account.

## Docs

- [ ] Integration docs or README notes show install and basic usage.
- [ ] Docs state that contextweaver routes/compiles context but does not execute provider tools.
- [ ] Any provider-specific limitations are documented.