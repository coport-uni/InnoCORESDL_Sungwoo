"""Hardware driver repos, tracked in-repo as git submodules.

The four drivers the cells use are installable packages (editable
installs from `requirements.txt`), so they are imported by *package*
name and not through this package: `sy01b`, `entris_ii`, `mks_motor`,
`LinearMotorController`.

This package exists for the two repos that are not packaged and are
imported by path instead — `external.HotplateController.<module>` and
`external.SmartPlugController.<module>`.

See SUBMODULES.md for what each repo is; `git submodule status` for the
pins. Clone with ``git submodule update --init --recursive``.
"""
