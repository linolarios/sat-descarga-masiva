"""Regenerate the committed tier-2 package fixture (deterministic, AGENT.md §12 (2)).

Run:  python tests/fixtures/generate_package_fixture.py
"""

from __future__ import annotations

from package_builder import DEFAULT_FIXTURES, FIXTURES, PACKAGE_NAME, build_package_bytes


def main() -> None:
    path = FIXTURES / PACKAGE_NAME
    path.write_bytes(build_package_bytes())
    print(f"wrote {path} ({len(DEFAULT_FIXTURES)} members: {', '.join(DEFAULT_FIXTURES)})")


if __name__ == "__main__":
    main()
