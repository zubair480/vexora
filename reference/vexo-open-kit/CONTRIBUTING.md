# For the kit's maintainers

Students do not need this page.

## Tests

For the kit's maintainers. These commands are typed in the kit folder itself
(`my-project/vexo-open-kit`), not in `my-project/`. The library tests, followed by the
safety checks:

    scripts/check.sh --tests

Or one part at a time:

    node --test js/test/*.test.js dashboard/test/*.test.mjs
    (cd python && python3 -m unittest discover -s tests)
    python3 -m unittest discover -s examples/test
    node --test examples/test/*.mjs
    python3 -m unittest discover -s projects/test
    node --test projects/test/*.test.mjs

The tests use the simulator. They need Python 3.9 or newer and Node 18 or newer, and
nothing else. The project page tests also use Node 22 and Chrome or Edge, and are skipped
without them.

## Contributing / safety checks

Run this once after you clone:

    git config core.hooksPath .githooks

From then on the pre-push hook runs `scripts/check.sh` before anything leaves your computer.
It scans for keys and passwords with [gitleaks](https://github.com/gitleaks/gitleaks#installing)
(install it first), refuses key and firmware files, and checks a private word list that only
the maintainers have. If you do not have that list, push with `KIT_DENY_SKIP=1 git push`;
ask a maintainer to push for you.
