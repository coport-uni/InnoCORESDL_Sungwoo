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

## 2026-07-27 — L2 Orchestrator: reflect `docs/L2_ORCHESTRATOR_SPEC.md`

Scope: build the orchestrator so the real-hardware verification path
(spec §9: dry run → real step_mode → real) is ready to execute the day
the devices are connected. No hardware was available for this work.

- [x] `docs/L1_AUDIT.md` (M0): A1–A8 judged from the L1 source, equipment
      mapping recorded, 4.2 smoke-test rows opened as `pending`, and six
      gaps listed (GAP-1 … GAP-6).
- [x] **GAP-1 found**: `BalanceLinearCell.stop()` is a no-op, so
      `POST /v1/stop` cannot halt the linear rail — the software e-stop is
      ineffective on the exact cell the first demo drives. Stated in the
      engine's `abort` docstring, the config, the demo scenario and
      LearnedPatterns #7. Needs user approval + `ADDING_A_CELL.md` to fix.
- [x] `orchestrator/` package (M1, M2, M4, M5): registry/config, httpx
      client, scenario models + `${...}` interpolation + safe `assert`
      evaluation, OpenAPI-driven dry-run validator, run state machine with
      per-cell locks, runlog, `/v1` FastAPI surface, `python -m
      orchestrator serve|validate|run` CLI.
- [x] `scenarios/demo_linear_move.yaml` — written from `server/routes.py`
      and `server/schemas.py`, not from the spec's draft examples
      (LearnedPatterns #5).
- [x] Deployment (M6 groundwork): `deploy/systemd/cell@.service` (one
      template unit, instance name selects the config),
      `deploy/docker-compose.orch.yml` + `Dockerfile.orch` +
      `requirements-orch.txt`, `deploy/README.md`, and the per-NUC configs
      `server/nuc1/`, `server/nuc2/`.
- [x] `claude_test/`: 27 tests over httpx `MockTransport` (M2/M4/M5
      acceptance) — all passing — plus `smoke_l1.py`, the
      operator-gated M0 smoke-test runner.
- [x] `docs/L2_ORCHESTRATOR_SPEC.md` → v0.9: the §8.1/§8.3 examples were
      corrected to the real L1 field names.
- [x] `ruff check` + `ruff format --check` pass on `orchestrator/` and
      `claude_test/`.
- [x] Register a GitHub issue via `gh issue create` — issue #4.
- [ ] **Bench verification — nothing here has touched hardware.** When the
      devices are connected: fill in the real NUC IPs and USB identifiers,
      run `claude_test/smoke_l1.py` per cell under operator supervision,
      write the measurements into `docs/L1_AUDIT.md`, calibrate the
      scenario timeouts from them, then run `demo_linear_move` as dry run →
      `--step-mode` → automatic.
- [x] GAP-2 closed in code — see the Cell D entry below.
- [ ] M7: the web scenario tab (`web/` was removed with the other pre-L2 work; git history has its last state).

## 2026-07-27 — Cell D (cell5): pump + single Z + hotplate + IR lamp

Composition confirmed by the user: syringe pump + 1 MKS motor (standalone
Z) + 1 hotplate + 1 Tapo plug. The hotplate is newly part of Cell D — it
was a TBD standalone item through spec v0.9.

- [x] Restored `server/` (user approved) — the new routes live there.
- [x] `cell/cell_protocol.py`: three new action sets (`zstage`,
      `hotplate`, `lamp`); the other two cells got defensive `_absent()`
      stubs so a misdirected call is a 409, not an AttributeError.
- [x] `cell/pump_z_thermal_cell.py` (`PumpZThermalCell`). Z motion uses
      the driver's group helpers with a **one-motor group** (that is what
      absorbs the limit-drop quirk; the paired-Z desync interlock is what
      does not apply). `stop()` kills motor + pump + heater + stirrer +
      lamp, attempting all five even if one fails.
- [x] `server/`: schemas + routes for the three action sets, optional
      Cell D fields on `StatusResponse`, `_load_pump_z_thermal()`, shape
      auto-detection from `[zstage]`/`[hotplate]`/`[lamp]`, and
      `--cell pump_z_thermal`.
- [x] `server/nuc2/cell5.toml.example` with a cell-level `max_celsius`
      ceiling checked before the driver is called.
- [x] Renamed the heater/lamp field `on` → `enabled`: YAML 1.1 resolves a
      bare `on:` **key** to a boolean, so a scenario could never address
      it (LearnedPatterns #8). Assert expressions now accept both the
      Python and the JSON spellings of the literals.
- [x] L2: `motion_prefixes` → `hazard_prefixes` (+ `zstage/`, `hotplate/`,
      `lamp/`), and the confirmation gate is now method-aware so read-only
      GETs are never gated. A heater starting unattended is gated like a
      motor starting unattended.
- [x] `scenarios/demo_cell_d_warmup.yaml`; `claude_test/smoke_l1.py` gained
      `zstage` / `hotplate` / `lamp` suites.
- [x] Tests: 32 pass, including a drift guard that compares the fake L1's
      routes **and request fields** against the real `create_app()` OpenAPI.
- [x] Verified end-to-end against the real L1 app driving a stub Cell D:
      validate → 14-step run with the operator gate → cell left safe
      (heater off, lamp off, Z home). **No hardware involved.**
- [x] Docs: spec v1.1, `docs/L1_AUDIT.md` (A1/A8/GAP-2/GAP-8 + smoke rows
      S5–S7, S10), CLAUDE.md, README, ADDING_A_CELL, deploy/README.
- [x] Register a GitHub issue via `gh issue create` — issue #5.
- [ ] **Bench bring-up (nothing here touched hardware):** fill in the Z
      motor's FTDI serial, the hotplate port (or rely on VID:PID
      auto-detect), the plug name + `secure.env`, and confirm port 17062
      against ARCHITECTURE.md. Then `smoke_l1.py --suite lamp --suite
      hotplate --suite zstage` under operator supervision.
- [ ] GAP-8 (new): the L2 lock is per cell, so two different cells sharing
      one physical workspace can still collide in a `parallel` block. Needs
      a `workspace` concept before a robot arm reaches into a cell's frame.

## 2026-07-28 — README, bench runbook, and the tools to execute it

- [x] `README.md` rewritten around **status**: what is built, what was
      verified without hardware, what has never run. Added the `/v1` action
      set table, the roadmap (M0–M7 with real states), and the two safety
      gaps up front.
- [x] `docs/L1_BRINGUP.md` — the bench runbook: safety rules that override
      everything, the TBD-collection step, per-cell start/identity/smoke
      procedure, the two contract probes, recording, and the hand-off to L2.
- [x] `claude_test/preflight.py` — read-only pre-flight: enumerates attached
      devices, resolves every config address, flags `TBD-…`, checks port
      collisions. Runs off-bench and reports what is missing.
- [x] `claude_test/smoke_l1.py` — added `--suite stop` (A4) and
      `--suite concurrency` (A7) with `--motion`, closing the two audit
      items that had no script.
- [x] **GAP-9 found while writing the stop probe** and measured off-bench:
      `POST /v1/stop` takes the same `app.state.lock` as the in-flight
      command, so it cannot preempt anything — on any cell. Move ran
      0.18→5.18 s, stop requested at 1.00 s, `cell.stop()` executed at
      5.18 s. Recorded in `docs/L1_AUDIT.md` (A4 rewritten, GAP-9, S9),
      `LearnedPatterns.md` #9, and every place that promised a software
      e-stop (engine `abort` docstring, orchestrator config, `/v1` API
      description, both demo scenarios, README).
- [x] Register a GitHub issue via `gh issue create` — issue #6.
- [ ] **Fix GAP-9** — needs user approval: serve `/v1/stop` lock-free like
      `/v1/health`, and give each driver a priority path for its stop
      command. The second half is upstream driver work.
- [ ] Bench bring-up itself (see `docs/L1_BRINGUP.md`).

## 2026-07-28 — NUC2 Cell D bring-up: pump-less shape, wait_s step, three bench demos

- [x] Verified the bench hardware: MKS Z on FTDI `NTB3EP5R`
      (NTREX USB2CAN, 0403:6001), IKA RCT digital on 0483:5740
      (`/dev/ttyACM0`, auto-detected), tapo_plug1 reachable at
      192.168.0.237 (P110M, secure.env in place). No syringe pump on the
      bus (1A86:7523 absent).
- [x] Made Cell D's pump optional: no `[pump]` table → the cell opens
      with Z + hotplate + lamp and answers 409 on pump routes
      (`cell/pump_z_thermal_cell.py`, `server/__main__.py`).
- [x] Lamp target may be a bare IP unknown to the submodule's
      device_list.md — the cell synthesises the entry
      (`_resolve_lamp`; LearnedPatterns #11).
- [x] Added the `wait_s` scenario step (local timed hold, abort-sliced,
      not hazard-gated) to `orchestrator/scenario.py` + `engine.py`;
      spec §8.1 updated; 8 new tests.
- [x] Three Cell D bench scenarios: `demo_cell_d_lamp_blink.yaml`
      (3 blinks ≈ 5 s), `demo_cell_d_hotplate_30c.yaml` (30 °C, 10 s
      soak), `demo_cell_d_z_cycles.yaml` (home + 3 top-to-bottom
      strokes). All validate + run against FakeL1 (40/40 tests pass).
- [x] Real NUC2 configs written (gitignored): `server/nuc2/cell5.toml`,
      `orchestrator/config.toml` (cell5 @ 127.0.0.1:17062).
- [x] Python env bootstrapped in `.venv/` (LearnedPatterns #10).
- [ ] Bench verification: blocked on USB permissions (dialout group +
      raw-USB udev rule need sudo), then L1 server up → validate →
      operator-gated runs of the three demos.
- [x] Register the GitHub issue for this bring-up via `gh issue create` —
      issue #8. (Needed a one-time `gh auth login` by the operator; the
      `gh` CLI itself was installed to ~/.local/bin, no sudo.)

## 2026-07-28 (cont.) — until: polled GET, lamp+heat-to-40C run, bench recovery

- [x] Root-caused the RCT wedge chain: hotplate's USB interface is
      USB-powered (hotplate power-cycle alone never resets it) and the
      shared USB hub was destabilizing it — moved to a DIRECT NUC port
      after a full drain (USB out -> power off -> 20 s -> direct port
      -> power on): sweeps went 2/10 -> 10/10. ModemManager disabled
      and udev MODE 0666 + ID_MM_DEVICE_IGNORE rules installed.
- [x] Z motor CAN silence after the hub bounces — restored by the
      operator re-powering the motor driver; SETUP OK.
- [x] Added the `until:` polled GET to the scenario language (spec
      §8.1): GET-only, `${result.*}` condition, `poll_s` interval,
      `timeout_s` bounds the whole poll, per-read cell lock so abort
      never queues. 4 new tests; FakeL1 hotplate now warms 5 C per
      state read while heating. 44/44 pass.
- [x] `scenarios/demo_cell_d_lamp_heat_40c.yaml` — lamp on, heat to
      40 C, shut heater+lamp on arrival (user-requested sequence).
- [x] Bench: diagnose ok (pump absent), hotplate/state 10/10 over
      HTTP, all four Cell D scenarios validate ok against the live L1.
- [x] Real run `cell_d_lamp_heat_40c` submitted through the
      orchestrator API; operator confirmed the hazard gate.
- [x] `until:` tolerance fix after the first real run hung at 39.0 C
      (readback granularity + control band; LearnedPatterns #14). Rerun
      COMPLETED 13/13 steps: lamp on -> 40 C -> heater+lamp off, final
      state safe. Runlog runs/20260728T093205Z-cell_d_lamp_heat_40c.
- [x] LearnedPatterns #13 (USB-powered interface + hub root cause,
      full-drain recovery, direct-port rule) and #14.
- [x] Real run `cell_d_z_cycles` COMPLETED 17/17 (z_top_mm=50 chosen by
      the operator): home 15.5 s, three 0<->50 mm strokes at ~3.6 s
      each, re-homed at the end. Runlog
      runs/20260728T093713Z-cell_d_z_cycles.
- [x] `scenarios/demo_cell_d_final.yaml` — the full user-specified
      sequence: home -> 400 mm -> home -> lamp+heater to 40 C ->
      2 min dwell -> everything off verified. Real run COMPLETED
      21/21 steps (z_up 10.5 s, wait_hot 208.9 s, dwell 120 s);
      final state z=0, heating off, lamp off. FakeL1 e2e test added
      (45/45). Runlog runs/20260728T094346Z-cell_d_final.
- [x] Documentation pass (user request): README Status rewritten around
      the Cell D bench verification (device identity -> L1 probes ->
      dry runs -> three gated real runs, with the results table and
      how-verified ladder), scenario list + wait_s/until + Cell D bench
      rules added; real NUC IPs (NUC1=192.168.0.126, NUC2=192.168.0.120)
      recorded in orchestrator/config.toml.example, the real
      config.toml (NUC1 cells staged as comments), and the spec's §2/§12
      examples.
