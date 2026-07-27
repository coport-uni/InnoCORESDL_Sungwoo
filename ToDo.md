# ToDo.md

Append-only task log per CommonClaude §4 Task Management. Never delete or
rewrite past entries; only add new tasks below and flip checkboxes.

---

## 2026-07-03 — Add vendor submodules & apply CommonClaude repo-wide

- [x] Add `vendor/CommonClaude`, `vendor/HotplateController`,
      `vendor/SmartPlugController` as git submodules.
- [x] Copy CommonClaude hooks + `settings.json` into `.claude/`
      (added a `vendor/` skip to the ruff post-write hook).
- [x] Update `CLAUDE.md`: Conventions section now defers to
      `vendor/CommonClaude`, new Hooks section, Files table rows.
- [x] Scaffold `ToDo.md` and `claude_test/README.md`.
- [ ] Register a GitHub issue via `gh issue create` (pending user
      confirmation / gh auth).

## 2026-07-03 — Register CommonClaude §7 MCP servers

- [x] Install runtimes in the container: `uv`/`uvx` 0.11.26
      (symlinked into `/usr/local/bin`), Node 18.19 + npm (apt).
- [x] Write project-scope `.mcp.json` with **serena**, **context7**,
      and **fetch**, per CommonClaude §7.3.
- [x] Handshake-test each server over stdio (JSON-RPC `initialize`).
- [ ] Persist `jq`, `ruff`, `uv`, and Node into the container image /
      setup script so they survive a rebuild.

## 2026-07-03 — demo_scenario: motor + hotplate/plug + syringe demo

- [x] Create `demo_scenario/` package: `main.py` holds only the
      scenario flow; `devices.py` holds all open/cleanup plumbing
      (context managers) over the vendored drivers.
- [x] Scenario order (user-specified): motor home → down
      `MOTOR_DOWN_MM`; plug ON + hotplate 30 °C for 1 min; syringe
      5 µL dispense (valve ports 2→1, M05 90° rule); motor to origin;
      heater + plug OFF together.
- [x] Add `ika>=2.0` and `python-kasa>=0.7` to `requirements.txt`.
- [x] `ruff check` / `ruff format --check` pass on `demo_scenario/`
      (and all non-vendor code).
- [x] Register a GitHub issue via `gh issue create` — issue #1
      (user authenticated gh; repo Issues feature was disabled,
      enabled via `gh api`).
- [ ] Bench validation on real devices: confirm single-motor "down"
      direction (flip `coord_invert` if reversed), plug credentials
      in `vendor/SmartPlugController/secure.env`.

## 2026-07-27 — Replace vendored drivers with `external/` submodules

- [x] Rename `vendor/` → `external/` (git mv; `.gitmodules` section
      names, `.git/config`, `.git/modules/`, and the nested submodule
      gitdir pointers all repointed).
- [x] Drop the copied-in-repo drivers (`sy01b`, `entris_ii`,
      `mks_motor`, `lmc`) — deleted by the operator, staged here.
- [x] Add 5 driver submodules: `SyringePumpController`,
      `LinearMotorController`, `PrecisionScaleController`,
      `MKSServo57DCANController`, `FR5Controller`.
- [x] Replace `external/VENDORED.md` with `external/SUBMODULES.md`
      (upstreams + pinned commits + bump procedure).
- [x] Repoint path references: `.claude/hooks/post-write-lint.sh`
      skip, `external/__init__.py`, `CLAUDE.md`, `README.md`,
      `ADDING_A_CELL.md`, `requirements.txt`.
- [ ] **Rewire driver imports.** `cell/` and `demo_scenario/` still
      import the old codenames (`external.sy01b`, `external.entris_ii`,
      `external.mks_motor`, `external.lmc`) which no longer exist.
      Strategy chosen by the user: **make each upstream repo an
      installable package** and import from there.

## 2026-07-27 — Make the driver submodules installable packages

Strategy (C) for the import rewire above: each `external/` repo ships a
`pyproject.toml` and is consumed as an editable install, not copied.

- [x] `SyringePumpController` — already packaged (`sy01b`, src layout);
      no upstream change needed.
- [x] `PrecisionScaleController` — added `pyproject.toml` (`entris-ii`,
      `src/entris_ii`). Pushed to upstream `main` (3d5d031).
- [x] `LinearMotorController` — added `pyproject.toml`
      (`linear-motor-controller`, flat module). Ships only
      `LinearMotorController.py`; the Modbus variant is deliberately
      excluded (user request). Pushed to upstream `main` (3992874).
- [x] `MKSServo57DCANController` — added `pyproject.toml`
      (`mks-servo57d-can`, flat module). Pushed to upstream `main`
      (501a381). Kept for reference only; see the MKS decision below.
- [x] Verified all four build: `pip wheel --no-deps` produces wheels
      with the expected module layout.
- [x] **MKS driver API gap resolved.** The deleted `vendor/mks_motor`
      (1108 lines, pyftdi) was a *different, more advanced* fork than
      `MKSServo57DCANController/mks_motor.py` (561 lines, ftd2xx), which
      lacks `prepare_usb_nodes`, `release_ftdi_sio`, `open(serial=…)`,
      `open_xz`, `home_xz`, `stop_group_hard`, `read_position_mm`,
      `_is_at_limit` — the paired-Z interlock CLAUDE.md §3 mandates.
      User chose to add the fork's origin repo as its own submodule:
      `external/ESP32S3BOX3MotorController` (kkhyunhho, 872df98). It is
      already packaged as `mks_motor` and exports exactly the names the
      cell imports. `MKSServo57DCANController` stays for reference.
- [x] `LinearMotorController` — added `resolve_port()` upstream so a
      `"VID:PID"` string (Moxa UPort 1150, `110A:1150`) resolves to
      `/dev/ttyUSBn` at open time; `__init__` runs the port through it.
      This replaces the deleted `lmc/__init__.py` shim, keeping the fix
      upstream rather than local. Pushed to `main` (02561f8).
- [x] Rewire the imports: `sy01b`, `entris_ii`, `mks_motor`,
      `LinearMotorController` — by package name, no `external.` prefix.
      `HotplateController`/`SmartPlugController` stay path-imported.
- [x] `requirements.txt` — the four drivers as `-e ./external/<Repo>`.
- [x] Verified in a throwaway venv: all four packages install editable
      and import; `MKSMotor` exposes `open_xz`/`home_xz`/
      `stop_group_hard`/`read_position_mm`/`move_sync`; `cell.*`,
      `server`, and `demo_scenario.devices` all import clean.
- [x] Confirmed the `sy01b` wheel's top-level `server` package does not
      shadow this repo's `server/` when running from the repo root.
- [ ] `FR5Controller` — still deferred (Cython `fairino/` SDK); no cell
      imports it.
- [ ] Bench validation on real hardware — nothing here was exercised
      against a device.
- [x] Add a root `ruff.toml` (line-length 80, `extend-exclude =
      ["external"]`) -- the repo had none, so `ruff format` defaulted to
      88 cols and fought the hand-wrapped 80-col code; reformatted 6
      files. Lint `select` stays at ruff defaults (the sibling repos'
      `E,W,I,N` flags 41 pre-existing issues, mostly `N803`/`N815` on
      `/v1` field names).
- [x] Register a GitHub issue via `gh issue create` -- issue #2.
