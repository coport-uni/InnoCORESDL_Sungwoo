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

## 2026-07-28 — cell4 bench bring-up attempt on NUC1 (blocked on hardware)

First attempt to bring up cell4 (balance + MINAS A6 linear rail) on NUC1
and drive `scenarios/demo_linear_move.yaml` through L2. **Blocked: the
scenario has still never run against hardware.**

- [x] Surveyed the bench: rail (Moxa `110A:1150` → `/dev/ttyUSB1`), pump
      (`1A86:7523` → `ttyUSB0`) and three FTDI adapters
      (`A10PUO5W`/`A10PUO5V`/`NTAMU6TO`) are attached. The **Entris-II
      balance (`24BC:0010`) is not on the bus at all** — no `ttyACM*`,
      and `PrecisionScaleController.find_port()` returns `None`.
- [x] Python env: no `sdl` conda env exists and conda refuses without a
      channel ToS acceptance, so built `.venv/` (already gitignored) from
      the Anaconda 3.13 interpreter and installed `requirements.txt`
      (4 editable driver installs) + `pytest`, `pytest-asyncio`,
      `ruff==0.12.0`.
- [x] Wrote `server/nuc1/cell4.toml` and `orchestrator/config.toml` for
      this bench. Corrected the address book: this bench is on
      **`192.168.0.0/24`** (NUC1 = `192.168.0.126`), not the
      `192.168.1.x` the `config.toml.example` placeholders assumed.
- [x] `pytest claude_test` 32 pass; `ruff check` + `ruff format --check`
      clean on the repo-pinned ruff 0.12.0.
- [x] **Enhanced `claude_test/preflight.py`** — it had reported
      "Pre-flight clean. Start the server" for a bench that could not
      start. Added `AUTO_DETECT` (resolves an unset `port` against the
      driver's own detector signature: balance `24BC:*`, IKA RCT
      `0483:5740`) and `_access_problem()` (`os.access` on every resolved
      port, verdict `PERM` with the exact fix). Both now count as
      blockers, so the exit code is 1. Still opens no serial handle.
      Recorded as `LearnedPatterns.md` #10; `claude_test/README.md` row
      updated.
- [ ] **BLOCKER 1 — `dialout`:** `/dev/ttyUSB*` is `root:dialout 660` and
      `getent group dialout` is empty, so the rail's port raises
      `EACCES`. Fix: `sudo usermod -aG dialout $USER`; no re-login needed
      over SSH since `sg dialout -c "…"` is setuid-root.
- [ ] **BLOCKER 2 — balance absent:** plug in the Entris-II and set the
      front panel to SBI / `AUTO W/` / `V.FAST`. `BalanceLinearCell.open()`
      requires the balance unconditionally, so cell4 cannot start without
      it even for a linear-only scenario.
- [ ] Decide whether to make the balance optional in `BalanceLinearCell`
      so the rail can be verified alone (offered to the user; not done).
- [ ] Confirm the rail's safe stroke — `demo_linear_move.yaml`
      `target_mm: 50.0` is still the spec's placeholder.
- [ ] Then: `preflight.py` (exit 0) → `python -m server` → `validate` →
      `run --step-mode`, operator at the physical e-stop (GAP-1/GAP-9:
      this rail cannot be stopped from software).
- [ ] Register a GitHub issue via `gh issue create` — **blocked on
      authentication.** `gh` was not installed, so v2.63.2 was fetched to
      `~/.local/bin/gh`; it runs, but this host has no GitHub credentials
      at all (no `~/.config/gh/hosts.yml`, no `GH_TOKEN`/`GITHUB_TOKEN`,
      no git credential helper). `gh auth login` is an interactive
      browser/device flow that only the operator can complete. Run
      `gh auth login` once, then file the issue for this entry.

## 2026-07-28 (cont.) — cell4 L1 + L2 brought up on real hardware

Continuation of the entry above, after the operator cleared the two
hardware blockers. **cell4's L1 server has now started against real
hardware for the first time, and `demo_linear_move.yaml` validates
end-to-end against it.** The motion run itself is still outstanding.

- [x] **Balance blocker resolved — charge-only USB-C cable.** Diagnosed
      from the total absence of kernel events for VID `24bc` over 7 days
      (a failing-but-present device logs enumeration errors; a dataless
      cable logs nothing). With a data cable: `usb 3-2.1 ... idVendor=24bc,
      idProduct=0010` → `cdc_acm ... ttyACM0`. See LearnedPatterns #11.
- [x] **`dialout` blocker resolved** — both `/dev/ttyUSB4` (rail) and
      `/dev/ttyACM0` (balance) now open from Python.
- [x] `claude_test/preflight.py` now reports `2/2 addresses resolve`,
      exit 0 — and the auto-detect check added earlier is what confirms
      the balance rather than deferring it.
- [x] **Driver gap found and fixed upstream (LearnedPatterns #12).**
      `cell/balance_linear_cell.py` called `set_ambient()`, `tare()` and
      `flush_pending_reads()`, none of which had ever existed in
      `external/PrecisionScaleController` (checked with `git log -S`
      across all 66 commits). Implemented on branch
      `feat/ambient-tare-flush`, commit `2c6a9b1`, sourced from the
      Technical Note p.4 command table (`Esc K/L/M/N`, `Esc T`) and
      hardware-verified on BCE224I-1SKR.
- [x] **L1 cell4 up**: `/v1/health` `cell_up=true`; `/v1/diagnose`
      reports balance `Model  BCE224I-1SKR` and amp `MDDLN45SL`
      `Ver.1.016`; `/v1/status` reads the rail at `stage_x_mm = 0.391`.
- [x] **L2 validates against the live cell**:
      `orchestrator validate scenarios/demo_linear_move.yaml` →
      `demo_linear_move: ok (9 steps)`, exit 0. This exercises the real
      contract — the validator pulls cell4's `/openapi.json` and checks
      every action path and body field against it.
- [ ] **Motion run — operator only.** `--step-mode` and
      `confirm_first_motion` gate on a console `input()`; answering that
      prompt on the operator's behalf would be fabricating a safety
      confirmation, so it is deliberately not automated. Agreed plan is
      10 mm first, not the spec's 50 mm placeholder:
      `python -m orchestrator --config orchestrator/config.toml run \
       scenarios/demo_linear_move.yaml --step-mode --param target_mm=10.0`
      Note `linear/home` is `move_to_mm(0.0)`, an absolute move, not a
      limit seek — from 0.391 mm that is a 0.4 mm move.
- [ ] **Fix the AUTO W/ ID-reply race (LearnedPatterns #13).**
      `/v1/diagnose` returned `serial_number = "G     +   0.0013 g"` — an
      auto-pushed weight landing between an ID command's write and its
      read. `get_model_number`/`get_serial_number` must reject lines that
      parse as weights instead of trusting arrival order.
- [ ] **Pump port is flapping (LearnedPatterns #11 corollary).**
      `usb 3-7-port1: disabled by hub (EMI?)` × 118 since 16:07:09, with
      `device descriptor read/64, error -32` and over-current count 0.
      The pump was stable on `3-2.4` for hours before the rewiring. Does
      not affect cell4, but cell1/cell5 will drop mid-dispense until the
      cable or hub port is changed. Ports `3-2-port1/3/4` are free.
- [ ] Push `feat/ambient-tare-flush` and bump the submodule pin —
      **blocked**: no GitHub credentials on this host
      (`could not read Username for 'https://github.com'`).

### Follow-up within the same session — /v1/health regression found and fixed

- [x] **`/v1/health` was 500ing on a healthy cell.** `diagnose()` queried
      the balance model and the amp's software version **twice** each per
      request; the second read of a back-to-back pair returned `None` on
      the real amp, and `/v1/health` serves `driver_versions` from the
      cached diagnose, so one bad read poisoned every later health call.
      Fixed in `cell/balance_linear_cell.py` (read once, reuse).
      LearnedPatterns #14.
- [x] Halving the SBI traffic also fixed the #13 symptom in practice —
      `serial_number` has returned `SerNo.    0047304196` on every call
      since (8/8), where it previously returned a weight. The underlying
      race is only mitigated, not cured; #13 stays open.
- [x] `HealthResponse.driver_versions` widened to `dict[str, str | None]`
      in `server/schemas.py`. Measured after the de-duplication,
      `read_software_version()` still returns `None` about **1 call in
      5**, so the liveness probe must report an unreadable version as
      `null` rather than 500. Verified: 3 diagnose+health cycles clean.
- [ ] **Root-cause the amp's intermittent `None`.** `read_software_version()`
      failing 1-in-5 is a MINAS RS485 comms defect in
      `external/LinearMotorController`, not a schema problem. It has no
      retry — `_send_and_receive` returns `None` on a failed handshake and
      callers pass it straight through. Deliberately not touched today:
      changing the motion driver's comms layer immediately before a
      motion run is the wrong order.
- [x] `pytest claude_test` 32 pass, `ruff check` + `ruff format --check`
      clean after all of the above.

### First motion run — aborted; a fabricated-success defect found

- [x] **Ran `demo_linear_move.yaml --step-mode --param target_mm=10.0`**
      (operator at the e-stop). `home` reported ok, `verify_home` passed,
      `move_out` failed `HTTP 500`. Run
      `20260728T080648Z-demo_linear_move`, state `failed`.
- [x] **Immediate cause**: the Moxa UPort dropped off USB mid-run —
      `termios.error: (5, 'Input/output error')` from
      `reset_input_buffer()` on a handle whose device had gone. It
      re-enumerated 17 s later on a *different* root port (`3-2.2` →
      `3-1`), so the running server was left holding a dead fd. Restarting
      the server was enough to recover; `preflight.py` re-resolved
      `110A:1150` to the new `/dev/ttyUSB5` with no config change, which is
      exactly what the VID:PID form is for.
- [x] **The far more serious finding (LearnedPatterns #15)**: the `home`
      step never moved the rail, yet reported success *and* satisfied its
      own verification assert. `cell/balance_linear_cell.py` was
      converting a failed RS485 read (`None`) into a plausible number in
      three places — `status()` → `0.0`, `home_linear()` → `0.0`,
      `move_linear()` → **the requested target**. Each substituted value
      is the one that makes the scenario's `verify_*` assert pass, so a
      dead link was self-confirming rather than merely silent.
- [x] **Fixed**: `_settled_mm()` now raises `TransportError` (503) for the
      motion paths; `status()` reports `null`; `StatusResponse.stage_x_mm`
      / `stage_z_mm` widened to `float | None`. Measured live afterwards:
      five consecutive `/v1/status` calls gave `0.39, 0.39, null, 0.39,
      0.39` — roughly **1 read in 5 fails**, and each of those would
      previously have reported the rail at the origin.
- [x] `pytest claude_test` 32 pass; ruff clean.
- [ ] **BLOCKER for any further motion — fix the amp transport.**
      `LinearMotorController._send_and_receive` has no retry: one bad
      handshake becomes `None`. At a 1-in-5 failure rate a multi-step
      motion scenario cannot complete, and the cell will now (correctly)
      503 instead of pretending. Needs a bounded retry in the driver,
      upstream. Until then `demo_linear_move.yaml` will keep failing part
      way — which is the honest outcome, not a regression.
- [ ] **Also fix the physical link first.** Both the pump port
      (`3-7-port1`, "disabled by hub (EMI?)" ×118) and now the rail's Moxa
      adapter have dropped out today. A 1-in-5 protocol failure may partly
      be this. Given GAP-1/GAP-9 mean the rail has **no software e-stop**,
      losing its control link mid-move is a genuine hazard, not a nuisance.

### Transport fixed — ready for the second motion attempt

- [x] Operator re-did the bench wiring. The rail's Moxa adapter now sits
      on a **direct root port** (`3-1`) instead of a hub, and has logged
      no USB events since 17:08. The balance is stable on `3-2.1`.
      (The pump's `3-7-port1` is still flapping — 735 events in 10 min —
      but no cell4 device is on that hub port.)
- [x] Re-measured the RS485 read failure rate after the rewiring: **2 in
      20 (10%)**, down from ~20% but still fatal to a closed-loop move.
- [x] **Added a bounded retry to `external/LinearMotorController`**
      (branch `fix/rs485-read-retry`, commit `882dbb0`): the handshake is
      now `_exchange()`, and `_send_and_receive(block, attempts)` retries
      it. Applied to the four read-only commands only; writes and
      execution-rights keep single-shot semantics because they are not
      idempotent. LearnedPatterns #16.
- [x] **Verified: 30 consecutive position reads, 0 failures.**
- [x] `orchestrator validate` → `demo_linear_move: ok (9 steps)` against
      the live cell.
- [ ] Second motion attempt — operator only (console `input()` gate).
- [ ] Push both driver branches and bump the two submodule pins:
      `PrecisionScaleController` `feat/ambient-tare-flush` (2c6a9b1) and
      `LinearMotorController` `fix/rs485-read-retry` (882dbb0).
      Still blocked on GitHub credentials.

### Second motion attempt — the rail really moved; return leg stalled

Run `20260728T082028Z-demo_linear_move`, `--param target_mm=10.0`,
`--step-mode`. **8 of 9 steps passed against real hardware.** Every number
below is a genuine device reading — the fabrication paths from
LearnedPatterns #15 are gone, so this is the first run whose results can
be trusted.

| step | result |
|---|---|
| `check_status` | 0.39 mm |
| `home` | **-0.03 mm** (2.435 s) |
| `move_out` | **10.097 mm** (7.856 s) |
| `read_back` | **10.104 mm** — confirmed via a second endpoint |
| `move_back` | **0.676 mm** (9.938 s) — target was 0.0 |
| `verify_back_home` | **FAILED**: `0.676 <= 0.1` |

- [x] The retry fix held: no `None`, no 503, no transport error anywhere
      in the run. Outbound positioning was accurate to 0.097 mm.
- [x] **Root cause of the failure is `move_to_mm`'s convergence loop**,
      not the transport. It aborts the moment the residual fails to
      shrink in a *single* iteration
      (`if abs(error_mm) >= prev_abs_error: return current_mm`), with
      `max_iterations = 5`. The return leg hit that and gave up 0.676 mm
      short.
- [x] **The deeper defect**: `move_to_mm` returns `current_mm` on all
      three exits — converged, stalled, and iteration-cap — so a caller
      cannot tell success from surrender. `move_linear` therefore
      answered `200 OK` with a position that was not the commanded one.
      This is the same failure shape as #15 (a failure indistinguishable
      from success), one layer further down; #15's fix only covers
      `None`, not "returned a number but never got there". The
      scenario's `verify_back_home` assert is what caught it.
- [ ] **Fix the convergence contract** (deliberately not done today —
      the operator was mid-run and this is the motion driver): tolerate a
      few consecutive non-improving iterations before declaring a stall,
      and make the converged/stalled distinction visible to the caller so
      the cell can raise instead of reporting a false success.
- [ ] Operator asked to re-run at **50 mm** for visual confirmation
      (10 mm is hard to see). Expect `verify_back_home` to fail the same
      way — the stall is in the return leg's convergence, not distance
      dependent — while `move_out` gives the visible travel wanted.

### Convergence contract fixed (operator asked for it before the 50 mm run)

- [x] `external/LinearMotorController` branch `fix/rs485-read-retry`,
      commit `d61d51f`: `move_to_mm` now returns
      `MoveResult(position_mm, converged, reason)` from every exit
      instead of a bare float, `stall_patience=3` replaces the
      abort-on-first-non-improvement, and `max_iterations` goes 5 → 12.
      The driver's own `server.py` updated: a non-converged move reports
      `state="error"` with the position actually reached.
      **BREAKING**: return type changed; taken deliberately since the
      alternative is a silent mis-position and the driver is 0.y.z.
- [x] `cell/balance_linear_cell.py::_settled_mm` now raises
      `DeviceFaultError` (HTTP 500) naming where the rail stopped, on top
      of the existing `TransportError` for an unreadable position.
- [x] Contract verified without hardware: `converged` → position
      returned; `stalled` / `iteration_cap` → `DeviceFaultError`;
      `None` → `TransportError`. `pytest` 32 pass, ruff clean both repos.
- [x] Server restarted on the new driver; `/v1/status` reads 0.698 mm and
      `validate` → `demo_linear_move: ok (9 steps)`.
- [ ] **Not yet proven on hardware**: no move has been commanded since
      the change, so whether the return leg now converges is still
      unverified. That needs the operator-gated run.

### Stop-command defect found and fixed (the real cause of the oscillation)

- [x] **50 mm run failed** at `move_out`, stalled at 48.592 mm after
      29.9 s — but the new contract worked exactly as intended: HTTP 500
      `linear rail did not reach its target (stalled); it stopped at
      48.592 mm`, instead of silently reporting success.
- [x] **Diagnosed from the per-iteration log**: corrections moved far
      further than commanded (`+1.408 mm @ speed 6` → **+13.0 mm**), with
      travel tracking commanded speed rather than commanded distance.
- [x] **Root cause**: `move_relative`'s `finally` block discarded the
      result of the zero-speed write. Measured on the amp: single-shot
      stop succeeded **28/30** — about one stop in fifteen was lost, so
      the rail kept running at the latched speed. LearnedPatterns #18.
- [x] Fixed upstream (`fe1be36`): `_stop_motion()` retries up to 5 and
      `move_relative` raises `MotionStopError` if the amp never
      acknowledges. Verified **30/30**; 60 zero-speed writes left the
      stationary rail at 0.175 mm, confirming idempotency.
- [x] `cell/balance_linear_cell.py::_absolute_move` maps
      `MotionStopError` → `DeviceFaultError`, so the operator gets
      "THE RAIL MAY STILL BE MOVING" verbatim rather than a bare 500.
- [x] `pytest` 32 pass, ruff clean both repos.
- [x] **Corrected an earlier wrong hypothesis**: the overshoot was first
      blamed on the #16 read-retry. Measuring read latency with and
      without it (max 2020 ms vs 2084 ms; 4/40 vs 3/40 reads over 500 ms)
      showed retries were not the cause. The ~2 s worst-case read is
      inherent to `_exchange`'s timeouts and predates that change.
- [ ] **Still open — the blind window.** Even with stops working, a
      failed read inside `move_relative`'s poll loop can leave the rail
      unmonitored for ~2 s while it moves. A control loop should not use
      a 2 s read timeout; this needs a short-timeout read for polling.
- [ ] Re-run 50 mm (operator-gated) to confirm the oscillation is gone.

### Third motion attempt — outbound converged, return aborted on the rights exchange

Run `20260728T084527Z-demo_linear_move`, `--param target_mm=50.0`.
**7 of 9 steps passed.** The stop fix (LinearMotorController#22) worked:
the oscillation is gone.

| step | result |
|---|---|
| `home` | 0.043 mm |
| `move_out` | **50.03 mm** (27.7 s) — target 50.0 |
| `read_back` | **50.024 mm** — confirmed via `/v1/status` |
| `move_back` | **FAILED** HTTP 503, position unknown |

- [x] `stall_patience=3` proved load-bearing: the log shows "did not
      improve (1/3)" and "(2/3)" several times during `move_out`, each
      time recovering, converging on iteration 8. The old patience of 1
      would have abandoned the move at iteration 2.
- [x] `move_back` failed in `_acquire_execution_rights` — a single lost
      exchange aborted the whole return leg. Fixed upstream (`3a77a82`)
      by retrying the rights exchange; **the same misclassification as
      the stop write**, both from grouping commands as "writes" rather
      than by what re-sending does.
- [x] Verified before committing, per the operator's instruction:
      ruff clean in all three repos, `pytest` 32 pass, 5/5 cell contract
      cases, and on hardware acquire 39/40 → 40/40 with the rail unmoved
      across 120 exchanges.
- [x] Operator returned the rail to origin; re-verified afterwards —
      0.221 mm stable over three reads, both devices identifying, health
      green, `validate` 9/9, and zero rail/balance USB events in 5 min.
- [ ] Re-run the full 50 mm round trip; `move_back` is the step to watch.
- [ ] **Still open — per-iteration overshoot.** `move_out` needed 8
      iterations and 27.7 s because corrections overshoot badly
      (`-1.116 mm` commanded → `-8.178 mm` travelled). A failed read in
      `move_relative`'s poll loop leaves the rail unmonitored for ~2 s
      while it moves, since `_exchange` uses 2 s timeouts. A control loop
      should poll with a short timeout; tracked in
      coport-uni/LinearMotorController#23.

### PASSED — first end-to-end L1 + L2 run on real hardware

Run `20260728T085331Z-demo_linear_move`, `--param target_mm=50.0`,
`--step-mode`. **State: completed. All 9 steps passed.**

| step | result |
|---|---|
| `check_status` | 0.221 mm |
| `home` | 0.026 mm (2.7 s) |
| `move_out` | **49.959 mm** (13.9 s) |
| `verify_out` | `49.959 >= 50.0 - 0.1` |
| `read_back` | **49.95 mm** — independent confirmation via `/v1/status` |
| `verify_status_agrees` | passed |
| `move_back` | **-0.04 mm** (12.5 s) |
| `verify_back_home` | `-0.04 <= 0.1` |

- [x] **The original goal is met**: the L2 orchestrator drives cell4's
      rail over HTTP `/v1` only, out to a commanded target and back,
      with every assertion satisfied by a genuine device reading.
- [x] `move_out` fell from **27.7 s to 13.9 s** versus the previous
      attempt — the rights-exchange retry removes the lost exchanges
      that were forcing extra convergence iterations.
- [x] Every number here is trustworthy in a way earlier runs were not:
      the substitution paths (LearnedPatterns #15, #17) are gone, so a
      failed read or an abandoned move now raises rather than reporting
      a plausible number.
- [ ] Still open, not blocking: per-iteration overshoot remains large,
      because a failed read in `move_relative`'s poll loop leaves the
      rail unmonitored for up to ~2 s (`_exchange` uses 2 s timeouts).
      Tracked in coport-uni/LinearMotorController#23.
- [ ] Push the three driver branches, open PRs, bump the two submodule
      pins.
