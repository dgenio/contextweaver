"""Generate the API reference pages and navigation.

This script is executed by mkdocs-gen-files at build time. It walks
src/contextweaver, skips private modules (names starting with ``_``) and
the CLI entry-point (``__main__``), and emits one ``::: identifier``
reference page per public module. It also writes a ``SUMMARY.md`` consumed
by mkdocs-literate-nav to build the "API Reference" nav section automatically.

New modules added to the package are picked up on the next ``mkdocs build``
with no manual edits required here.
"""

from __future__ import annotations

from pathlib import Path

import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()

src = Path("src")
package_root = src / "contextweaver"

for path in sorted(package_root.rglob("*.py")):
    module_path = path.relative_to(src).with_suffix("")
    doc_path = path.relative_to(src).with_suffix(".md")
    full_doc_path = Path("reference", doc_path)

    parts = tuple(module_path.parts)

    # Skip the CLI entry-point unconditionally.
    if parts[-1] == "__main__":
        continue

    # Treat __init__ as the index page for the package directory.
    if parts[-1] == "__init__":
        parts = parts[:-1]
        doc_path = doc_path.with_name("index.md")
        full_doc_path = full_doc_path.with_name("index.md")

    if not parts:
        continue

    # Skip private helpers and private package directories (non-dunder names only;
    # __init__ is handled above, __main__ is handled above).
    if any(p.startswith("_") for p in parts):
        continue

    nav[parts] = doc_path.as_posix()

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        ident = ".".join(parts)
        fd.write(f"::: {ident}\n")

    mkdocs_gen_files.set_edit_path(full_doc_path, path)

with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
