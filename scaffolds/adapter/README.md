# Adapter Scaffold

This directory contains copy-paste templates for new contextweaver adapters. It is intentionally dependency-free: no cookiecutter package is required.

## Files

- `adapter.py.tmpl`: template for `src/contextweaver/adapters/<adapter_name>.py`
- `test_adapter.py.tmpl`: template for `tests/test_adapters_<adapter_name>.py`
- `integration_doc.md.tmpl`: template for `docs/integration_<adapter_name>.md`
- `checklist.md`: PR checklist for adapter contributions

## How to use

1. Choose a normalized adapter name, for example `example_provider`.
2. Copy `adapter.py.tmpl` to `src/contextweaver/adapters/example_provider.py`.
3. Replace placeholders such as `{{adapter_name}}`, `{{ProviderTool}}`, and `{{provider_package}}`.
4. Copy `test_adapter.py.tmpl` to `tests/test_adapters_example_provider.py` and fill in fake provider objects.
5. Copy `integration_doc.md.tmpl` to `docs/integration_example_provider.md` if the adapter needs user-facing docs.
6. Add any optional dependency wiring requested by maintainers.

## Required adapter properties

- Importing the adapter must not require provider credentials.
- Importing the adapter should not require the provider SDK unless unavoidable and documented.
- Tests must not make live network calls.
- Provider objects should be translated at the adapter boundary into contextweaver-native types or plain Python data.
- Public error messages should tell users which optional package or extra to install.