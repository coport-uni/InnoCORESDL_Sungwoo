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

## 2026-07-28 — scenarios/demo_weigh_at_position.yaml (the first balance scenario)

Asked for after the linear demo passed: the balance had still never been
exercised through L1 or L2. `demo_linear_move.yaml` says so in its own
description — "Single cell, **no balance**" — and neither existing
scenario touches it. Operator's requested flow: zero → 50 mm → operator
loads a vial → weigh → return to origin.

- [x] Closed the L1 gap first by hand, since the four balance endpoints
      had never been called: `GET /balance/weight` → `0.0005 g stable`;
      `POST /balance/ambient very_unstable` → accepted; the same with
      `bogus` → `InvalidArgError`; `POST /balance/tare` then a re-read →
      **0.0005 g → 0.0 g**, i.e. the balance actually zeroed rather than
      the call merely not raising. This is the first proof that the three
      driver methods added earlier work through the HTTP layer, not just
      when called directly.
- [x] Wrote `scenarios/demo_weigh_at_position.yaml` (15 steps), field
      names checked against `server/routes.py` and `server/schemas.py`.
- [x] `validate` → `demo_weigh_at_position: ok (15 steps)` against the
      live cell.
- [x] **Vial loading uses the `--step-mode` pause** — the scenario
      language has no wait step (spec §8.1). Documented in the file that
      running it unattended weighs an empty pan and fails
      `verify_vial_present`.
- [x] **`verify_zeroed` reads the weight back instead of asserting on
      `balance/tare`'s response.** `BalanceLinearCell.tare()` returns a
      hardcoded `0.0` without measuring, so an assert on it would prove
      only that the code ran — the same family as LearnedPatterns #15,
      benign here but worth not building a check on.
- [x] Absolute-value checks written as paired comparisons: the assert
      evaluator allows no function calls, so `abs()` is unavailable.
- [x] Added a closing `weigh_after_return` + `verify_no_drift`: the vial
      is unchanged, so the difference from the first reading is exactly
      what carrying the balance did to the measurement. That number
      decides whether cell4 can weigh anywhere other than where it
      settles.
- [ ] **Run it** (`--step-mode`, operator at the bench). Not yet run, so
      the balance has still never been driven from L2.
- [ ] Tune the guessed parameters from the first run: `min_vial_g` (0.5)
      needs the real vial mass; `max_drift_g` (0.05) should come from the
      measured drift rather than a guess — a failure there on the first
      run is itself the datum.
- [ ] Commit once it has run and the parameters are real (operator asked
      for test-before-commit).

### Vial mass supplied; rail adapter then failed at the USB layer

- [x] Operator gave the vial mass as ~23-27 g, so the presence check
      became a bracket: `min_vial_g: 20.0`, `max_vial_g: 30.0`. A lower
      bound alone catches only an empty pan; the bracket also catches the
      wrong object. Re-validated: `demo_weigh_at_position: ok (15 steps)`.
- [x] **The Moxa UPort then went into a hard USB fault**, so the run
      could not be attempted. `/v1/status` returned `stage_x_mm: null`
      four times running — not the intermittent ~10%, but every read.
      The kernel logs `ti_usb_3410_5052_1 ttyUSB5:
      ti_bulk_out_callback - nonzero urb status, -71` (EPROTO) every 2 s,
      and with the server stopped the port cannot even be opened:
      `OSError [Errno 5] Input/output error: '/dev/ttyUSB5'`. The balance
      on the same bus answers normally, so this is the adapter alone, not
      the bus. Needs a physical re-plug; no software path recovers it.
- [x] Worth noting: the nullable `stage_x_mm` from LearnedPatterns #15 is
      what made this visible immediately. Before that change the same
      dead adapter would have reported the rail sitting at 0.0 mm.
- [x] **Third failure of this adapter today** — dropped mid-run at 17:06,
      re-enumerated on a different port at 17:08, and now this. The pump's
      `3-7-port1` is also still flapping. Something physical recurs here;
      the connectors and cables want inspecting, not just re-seating.
- [ ] Re-plug the Moxa, restart the server, then run
      `demo_weigh_at_position.yaml --step-mode` with the vial to hand.
- [ ] `max_drift_g: 0.05` is still a guess and is the one parameter that
      should come from measurement. A 23-27 g load carried 50 mm may well
      exceed it; a failure there is the datum, not a defect.

### First weighing run — reached the balance, then stalled on the move out

Run `20260728T093102Z-demo_weigh_at_position`, after the operator power
cycled the linear rail. **The balance half worked**: `home`, `tare` and
`confirm_zero` all passed, which is the first time the balance has been
driven from L2 at all. Then:

```
move_to_weigh  ->  HTTP 500: did not reach its target (stalled);
                   it stopped at 51.846 mm
```

- [x] Bench was healthy going in: rail reads **50/50**, median 25 ms,
      max 27.9 ms — 538x margin against the 15 s step timeout, so the
      earlier timeout work was not implicated.
- [x] **Every bad iteration coincides with an RS485 error inside
      `move_relative`'s poll loop**, and the failures are not uniform:

      | iter | error in the loop | commanded | actual |
      |---|---|---|---|
      | 1 | `Response block receive timeout` | +50.049 mm | overshoot 1.534 mm |
      | 2 | `Received NAK` | -1.534 mm | **-12.579 mm** |
      | 4 | NAK, Incomplete, NAK, timeout | -1.837 mm | **+0.008 mm** |

      So a lost read can leave the rail running far past target *or*
      leave it standing still — the same fault produces opposite
      outcomes depending on where in the loop it lands.
- [ ] **Unverified hypothesis**: iteration 4 looks like the speed write
      (`Pr3.04`) failing — it is the one command still left single-shot,
      and "no motion, then a 10 s poll timeout" fits what the log shows.
      Writing a speed is a register write and therefore idempotent, so
      retrying it is probably as safe as the stop write was. **Not
      changed**: the adapter died before the measurement, and this
      session has already produced two changes that were defensible on
      paper and wrong on the bench (LearnedPatterns #18, #19). Measure
      the single-shot write success rate first.
- [ ] **The deeper limit, which retries will not remove.**
      `move_relative` latches a speed and then polls position over a
      9600-baud link to decide when to stop. Stopping accuracy is
      therefore "how far the rail travelled since the last successful
      read", on a link that fails ~10% of the time and can block for
      seconds. The convergence loop hides this by iterating, but each
      iteration's error is large and random. The real fix is the amp's
      own **position-control mode**, which moves the stopping decision
      inside the amp and off the serial link — a change of control
      strategy, not a patch, and worth planning separately.
- [ ] **Adapter failed for the sixth time today**, ~10 minutes after the
      power cycle, mid-measurement. No software change can be verified at
      this rate. Next step is physical: swap the UPort, or at least its
      USB cable. The balance on the same bus has not faltered once all
      day.

### Positioning tolerance relaxed to 2.0 mm (operator's choice)

Of the three options put to the operator — retry the speed write, switch
to the amp's position-control mode, or relax the tolerance — the last was
chosen, at 2.0 mm.

- [x] The value is not arbitrary. `tolerance_mm` is handed down to
      `move_relative_mm`, whose poll loop stops as soon as the remaining
      distance is inside it, so it governs **how the rail is driven**, not
      just what counts as arrival. Set above the measured coast
      (1.5-1.8 mm at speed 25) the first coarse move lands inside
      tolerance; set below it, the loop overshoots and then enters the
      small-correction regime where every bench failure occurred.
- [x] `external/LinearMotorController` `6786366`: `move_to_mm` default
      0.1 -> 2.0, with the measurement recorded in the docstring.
- [x] Both scenarios' `params.tolerance_mm` 0.1 -> 2.0. The scenario
      value must not be tighter than the driver's, or every run fails an
      assert on a rail the driver considers arrived.
- [x] ruff clean, `pytest` 32 pass, all three scenarios parse, driver
      default and scenario params confirmed equal. Recorded failures of
      1.846, 1.408 and 0.676 mm all fall inside the new tolerance.
- [ ] **Unverified on hardware** — the adapter has been faulted since
      before the change. The premise to confirm is that one coarse move
      lands inside 2 mm, removing the correction chatter entirely.
- [ ] 2 mm is a 20x relaxation and a real loss of precision, accepted to
      buy convergence over an unreliable link. Revisit if the bench needs
      finer positioning *and* the RS485 link becomes dependable — or if
      the position-control mode is adopted, which would make the
      trade-off unnecessary.

### Balance stopped auto-pushing; SBI reply queue is one behind

Operator reported the tare step taking ~40 s. Measured through L1:

```
balance/tare   : 17.1 s (first call) / 0.001 s (second)   HTTP 200
balance/weight : 30.0 s -> HTTP 500                       (both calls)
```

- [x] The 30 s failure is `read_stable_weight` hitting the driver's
      `STABLE_READ_TIMEOUT_S`. What the operator experienced as a slow
      tare is the `confirm_zero` step that follows it, not `tare`.
- [x] **The balance is sending nothing at all.** Listening raw on
      `/dev/ttyACM1` for 20 s captured **0 lines**. Under
      `COM.OUTP = AUTO W/` it should push a value on every stability
      event, so the auto-push stream is off — and `read_stable_weight`
      is passive, so it waits forever by design.
- [x] **The link is fine, but the reply queue is one behind.** Direct
      commands answer with the *previous* command's response:

      | command | reply |
      |---|---|
      | `get_model_number()` (`Esc x1_`) | `G     -   0.0003 g` |
      | `get_serial_number()` (`Esc x2_`) | `Model  BCE224I-1SKR` |
      | `Esc kP` | `SerNo.    0047304196` |

      This is LearnedPatterns #13 — the driver trusts the first CR-LF
      line it reads rather than matching the reply to the command. With
      auto-push off there is no traffic to hide it, so the one-line skew
      is now plainly visible instead of intermittent.
- [ ] **Operator action**: check the front panel —
      `DATA.OUT. -> COM. SBI -> COM.OUTP = AUTO W/` and
      `SETUP -> BALANCE -> STAB.RNG = V.FAST`. Neither is settable over
      SBI. The USB re-plug is the likely trigger.
- [ ] **Then re-measure and set the balance step timeouts from data.**
      `tare` is currently 30 s and `confirm_zero` 40 s, the latter
      overlapping the driver's own 30 s so it is unclear which would
      fire first. Deliberately not changed yet: the 17 s tare has no
      explanation — `tare()` writes `Esc T` once and should cost ~25 ms
      — and picking numbers before understanding that would just add
      more unfounded constants.
- [ ] **Fix #13 properly while here.** The queue skew is the root of
      both the wrong `serial_number` in `/v1/diagnose` earlier and this
      confusing diagnosis. The ID readers should reject lines that parse
      as weights instead of trusting arrival order.

### Balance adapted to the bench: settling judged in software

Operator set the panel to `COM.OUTP = AUTO.W/O` (automatic output
*without* stability), so the balance now streams whether or not it
considers itself settled, and the judgement moves into the driver.

- [x] Confirmed the stream: **2.5 lines/s**, all numeric. Before the
      change, 20 s of listening captured zero lines — `AUTO W/` only
      speaks after the balance calls itself stable, which on this bench
      never happened.
- [x] `set_ambient("very_unstable")` measurably helps: sample spread over
      15 s fell from **0.0095 g to 0.0036 g**.
- [x] **Calibrated the settling tolerance against the bench.** Spread
      across 3 consecutive readings: median 0.0006 g, p90 0.0015 g, max
      0.0037 g. The driver's `SETTLE_TOLERANCE_G = 0.002` sits between
      p90 and max — most reads converge at once, an occasional one waits
      a beat. It was guessed at twice the datasheet jitter and the
      measurement happens to endorse it; recording that it is now
      measured rather than assumed.
- [x] `read_settled_weight` on hardware: **5/5**, 1.2-3.2 s.
- [x] **The 17 s tare is explained and gone.** `tare()` now measures
      **0.1 ms**, as the protocol implies. The earlier 17 s was the
      dead-stream state, where the reply queue had slipped a line — not
      a property of tare at all.
- [x] `BalanceLinearCell.read_weight` switched to `read_settled_weight`
      with an explicit 30 s budget, deliberately under the scenarios'
      40 s step timeout so the driver's timeout — which names
      `AUTO.W/O` as the likely cause — fires before a generic one.
- [x] Verified through L1: `balance/weight` **1.0-1.2 s, HTTP 200**
      (previously 30 s then HTTP 500); `tare` 0.0015 s; a tare followed
      by a read returns 0.0056 g, inside the scenario's `empty_g` 0.01.
- [x] ruff clean, `pytest` 32 pass.

### Adapter exonerated: the fault follows the USB port, not the device

The UPort 1150 was blamed for seven failures today and recommended for
replacement. It was the wrong diagnosis twice over. Full write-up in
`LearnedPatterns.md` #20 and #21.

- [x] **Moved the UPort off the root port** (`3-1`) to the hub that
      carries the balance (`3-2.2`). The `-71` EPROTO storm went from
      **336/s to zero** and `serial.Serial()` opened first try, 10/10
      exchanges including the `_reconfigure_port` call that had been
      failing. Every device that had never faulted was already behind a
      hub; only the UPort was on a root port.
- [x] **Confirmed the remaining fault is electrical, and the kernel
      names it**: `disabled by hub (EMI?), re-enabling...` on
      `3-7-port1` (pump) and `3-2-port2` (UPort). Measured over 60 s —
      pump **14** re-enumerations, UPort **3** (device number 56 -> 66
      -> 73 -> 77 -> 81), balance on the *same hub* as the UPort **0**.
      Same hub and host, so this is per-port wiring, not the hub. The
      two affected devices are the two attached to motor-driven gear.
- [x] **Explained the instant HTTP 500** that killed run
      `20260728T101550Z-demo_weigh_at_position` at its first step
      (`check_status`, 0.002 s): the server held
      `/proc/<pid>/fd/6 -> /dev/ttyUSB3 (deleted)` after the adapter
      re-enumerated to `ttyUSB4`. VID:PID is resolved once at startup,
      so re-enumeration during a run is not covered.
- [x] Verified the full cell against real hardware while the link was
      briefly quiet: `diagnose` balance `BCE224I-1SKR` /
      `SerNo. 0047304196`, stage `MDDLN45SL Ver.1.016`,
      `ok_to_initialize: true`; `status` `stage_x_mm 0.405`;
      `balance/weight` 0.0008 g stable in 0.84 s; `validate` 15 steps ok.
      **The software is ready; the link is not.**

- [x] **Pump exonerated.** Pulling its USB made the UPort *worse*, not
      better: re-enumerations went 2 -> **12** per 40 s and
      `disabled by hub` 2 -> **19**. Two devices failing together did
      not make one the cause of the other.
- [x] **The servo amp is the emitter — confirmed by switching it off.**
      With amp power down, a 40 s window gives **0** on every counter:
      UPort re-enumerations, `disabled by hub`, urb errors, balance
      events. Over a minute of total silence after 18 re-enumerations
      per minute. The pump, now on `3-2.3` beside the UPort and the
      balance, is also **0** — so the pump's 118 dropouts on
      `3-7-port1` earlier today were this same amp all along, and the
      "EMI?" in the kernel's message was literal.
- [x] **Coupling path confirmed: conducted, through the RS485 pair.**
      Three conditions, 40 s each, UPort re-enumerations:

      | amp | RS485 cable | re-enumerations |
      |---|---|---|
      | off | connected | **0** |
      | on  | connected | **15** |
      | on  | **disconnected** | **0** (silent 4 min) |

      With the amp running, pulling one serial cable stops it. The noise
      travels the signal line and ground, not the air — consistent with
      the balance and pump, which have no galvanic path to the amp,
      staying at 0 while the UPort flapped. **So shielded USB cable and
      ferrites on the USB side are the wrong purchase.**
- [x] **Mains plug position is irrelevant** — moving it changed nothing
      (15 per 40 s before and after). Revert it if convenient.
- [ ] **Fix, cheapest first** (1 and 2 cost nothing and are inspection
      only):
      1. Grounding: confirm the amp's PE is actually landed; confirm the
         RS485 run is shielded twisted pair; confirm the shield is
         terminated at **one end only** (the amp). Shield landed at both
         ends makes a ground loop, which is this symptom.
      2. Signal common: MINAS A6 RS485 has an SG terminal. If SG is not
         run between amp and UPort the two ends float relative to each
         other and common-mode swings do exactly this. Check the
         wiring diagram.
      3. Common-mode choke: 3-4 turns of the RS485 cable through a
         ferrite core. Cents, and it targets the path now proven.
      4. Only if 1-3 fail: isolation — UPort **1150I** or an inline
         RS485 isolator, 2 kV, which breaks the path physically.
- [ ] Keep the UPort **off the root port** regardless — that fix is
      independent and it holds (urb `-71` has stayed at 0 since).

- [ ] **Separate fault, not cell4's: the pump flaps on its own.** 9
      disconnect cycles in 40 s on `3-2-port3`, after 20 min of silence
      on that same port, and 118 earlier in the day on `3-7-port1`. It
      follows the device across hubs and comes and goes, so suspect the
      pump's own USB cable. Affects cell1/cell5; chase it there.
- [ ] **Deliberately not doing: auto-reopen on `EIO`.** On a link that
      drops every 20 s it would lose the port during a 14 s move, and
      the rail has no software stop (GAP-1). Revisit only once the link
      is stable enough that reconnect covers the rare case.
- [ ] `demo_weigh_at_position.yaml` still has **never completed** — it
      has not yet got past its first step. `max_drift_g` (0.05) remains
      a guess until a run produces the two weight reads.

### Software adapted to a spotty link (reads survive; moves still abort)

The wiring fault is unfixed, so the bench was made usable around it.
Design and numbers in `LearnedPatterns.md` #26.

- [x] **Startup no longer loses a race with the adapter.** `_open_serial`
      waits out an enumeration gap (20 x 0.5 s) instead of taking
      `resolve_port()`'s "no adapter connected" at face value. Was
      failing 3 starts in 4; now first try.
- [x] **Reads survive the drop.** `_exchange` reopens on the OS-level
      error and reports no-reply so the existing retry loop covers it.
      150 s of `/v1/status` across 18 re-enumerations and 14 reconnects:

      | | reads | positions | null | median |
      |---|---|---|---|---|
      | before | 30 | 0 | 30 | 6112 ms |
      | after | 959 | 959 | 0 | 30 ms |

- [x] **Moves deliberately still fail across a reconnect.**
      `link_generation` is checked after every move and read inside
      `move_to_mm`; on a change the rail is stopped and the move raises
      `LinkDroppedError` (stop landed) or `MotionStopError` (it did
      not). The cell maps the former to `TransportError` — never a
      success. Reopening is safe for a read and unsafe for a move, and
      the rail has no software stop (GAP-1).
- [x] Driver `feat/reconnect-on-link-drop` 3b8f868 (11 tests, no
      hardware); parent f6b8623 bumps the pin. ruff clean, 61 pass.

- [ ] **Expect `demo_weigh_at_position.yaml` to abort on a move** while
      the link is this bad: re-enumerations run ~8 s apart and a 50 mm
      move takes ~14 s, so most moves will straddle one. That is the
      abort rule working, not a regression. The run is now *safe* to
      attempt with the physical e-stop in reach; it is not yet likely to
      complete.
- [ ] The wiring fix is still the blocker: SG between amp and UPort,
      120R termination once per end, shield grounded at one end only.
      Tracked as issue #13, which blocks #10.

## 2026-07-28 — cell1 XZ gantry bring-up, without its syringe pump

Tracked as [#14](https://github.com/coport-uni/InnoCORESDL_Sungwoo/issues/14).

Hardware for cell1 is on the bench except the SY-01B, whose USB link flaps
on its own (see the entry above). Goal: L1 + L2 driving the XZ gantry, then
a scenario that steps the frame 50 mm.

- [x] **The pump is now optional in `PumpGantryCell`.** Omit the config's
      `[pump]` table and the cell serves its gantry alone: `diagnose()`
      reports `pump.present=false`, every `/v1/pump/*` action answers 409,
      and `stop()`/`close()` skip it. Mirrors how cell4 handles its
      absent-by-design pump, so the cell does not have to be blocked on a
      device no gantry step needs. `server/nuc1/cell1.toml` written
      without the table.
- [x] **Corrected the X adapter serial: `NTAM63XD` → `NTAMU6TO`.** The
      documented value is not on this bench and `open_xz` raises on it, so
      the server would have died at startup. The two Z serials
      (`A10PUO5V`, `A10PUO5W`) were right, which is what made the wrong
      one look credible. Fixed in the `Config` default, `cell1.toml.example`
      and `CLAUDE.md`; matches upstream `bridge.py`. LearnedPatterns #22.
- [x] **Removed two fabricated-success paths from the gantry**, the same
      class #15/#17 removed from cell4's rail — found by reading the
      driver, not from a bench failure:
      - `move_gantry`/`home_gantry` returned the *commanded* target, so a
        gantry that never moved answered `200 OK` with the requested
        position. `MKSMotor.move_to` only *prints* `[ERROR] Motor failed
        to start`, and `move_sync` discards its return value. Now every
        move is confirmed by an encoder readback, with distinct errors for
        unreadable (`TransportError`), short (`DeviceFaultError`) and a
        desynced Z pair.
      - `diagnose()` hardcoded `stage.ok = True`. Now derived from a live
        read of all three motors.
      `status()` likewise serves live encoder values, `null` for an axis
      it could not read, and flags a racking Z pair in `error`.
      LearnedPatterns #24.
- [x] **21 unit tests** in `claude_test/test_pump_gantry_cell.py` (pump
      absent, silent motor, dropped move, unreadable axis, desynced Z).
      Full suite 61 passed; ruff clean.
- [x] **`scenarios/demo_gantry_step.yaml`** — home, X out 50 mm and back,
      Z out 50 mm and back, asserting the *travelled distance* between
      consecutive encoder readings rather than the endpoint, plus a
      `/v1/status` cross-check that must agree with what `gantry/move`
      returned. Dry run: 23 steps, no issues. Scoped to one 50 mm step per
      axis by operator decision — travel below Z is not yet measured.
- [x] `orchestrator/config.toml`: cell1 → `http://127.0.0.1:17054` (same
      NUC as cell4, so loopback).

- [ ] **BLOCKED on the udev rule — needs root, cannot be done from here.**
      `ftdi_sio` holds all three USB2CAN adapters and `/dev/bus/usb` nodes
      are `root:root`, so pyftdi cannot claim them and the cell server
      cannot start. `release_ftdi_sio()` is a no-op unprivileged. Install
      `SETUP_UBUNTU.md` §1's rule, then `udevadm control --reload-rules &&
      udevadm trigger`. Until then `preflight.py` reports the adapters as
      "not attached", which is a permission failure, not a missing
      adapter — LearnedPatterns #23.
- [ ] **Not yet run on hardware.** Everything above is code, dry run and
      offline tests. Still to do once the rule is in: `python -m server
      --config server/nuc1/cell1.toml`, `smoke_l1.py --suite gantry`, then
      the scenario in `--step-mode`.
- [ ] **`ARRIVAL_TOLERANCE_MM` (1.0) and `Z_DESYNC_LIMIT_MM` (1.0) are
      placeholders**, as is the scenario's `tolerance_mm`. Set all three
      from the first run's measured residual and Z spread; they must be
      tightened together or the scenario asserts and the cell's own check
      disagree.
- [ ] **X homes off the opposite end from the only validated script.**
      Three conventions exist for X and they do not agree:

      | source | `home_dir_x` | X `coord_invert` | +mm goes |
      |---|---|---|---|
      | `CVMeasure.py` (validated to 500 mm) | `0x01` | False | away from home |
      | `bridge.py` | `0x00` | False | **into the home limit** |
      | this cell | `0x00` | **True** | away from home |

      The cell is self-consistent — homing off the other end and
      inverting the coordinate cancel out, so `+mm` still travels into
      the working range. But its origin is the **opposite physical end**
      of the X rail from CVMeasure's, so any X coordinate taken from that
      script is mirrored here. `bridge.py` is the odd one out and looks
      simply untested for absolute X moves (its speed constants cite
      CVMeasure, not its directions). Nothing to change yet; confirm on
      the first run which end X homes to and write it down. If the sign
      is wrong after all, `move_to` drives into a closed limit and the
      new `_confirm` readback raises rather than reporting success.

### First end-to-end weighing run on cell4 — completed, 15/15

Run `20260728T111725Z-demo_weigh_at_position`, step-mode, git 869f8af.
The first run in which cell4's balance and rail worked together.

- [x] **The design premise is measured, and it holds.** The scenario
      exists to answer whether cell4 can weigh somewhere other than
      where it settles. Carrying the balance 50 mm and back moved the
      reading by **0.0039 g** (25.7424 -> 25.7385 g on a 25.7 g vial).
      `max_drift_g` was guessed at 0.05; the measurement is 13x smaller.
- [x] Balance: tare -> 0.001 g on an empty pan, vial 25.7424 g stable,
      both reads ~0.8-1.0 s. Rail: `move_to_weigh` 49.478 mm in 6.2 s.
- [x] **The link dropped during the run and the software absorbed it.**
      One UPort re-enumeration at 20:18:20, inside the `weigh` step. Both
      moves happened to get clean windows (20:17:42-49, 20:18:27-33) —
      partly luck, since a drop during a move would have aborted it by
      design. This run is not yet reproducible; issue #13 still blocks.

- [ ] **`home` did not move, and the run cannot tell you it did.** It
      started at 0.177 mm and returned in **29 ms**: inside the 2 mm
      tolerance, `move_to_mm` reports `already_in_tolerance` without
      issuing motion. `verify_home` passing is therefore not evidence
      that homing works. Worth a scenario that homes from a known
      non-zero position before anyone relies on it.
- [ ] **`move_home` had 0.2 mm of margin left.** It landed at
      **-1.797 mm** against a +/-2.0 mm assert, having overshot the
      origin. The 2 mm tolerance was chosen to sit *above* the measured
      coast (1.5-1.8 mm) so the first coarse move lands inside it — but
      the overshoot came in at the top of that range, so there is no
      headroom. Either widen the assert or tighten the approach speed;
      do not leave it at a value the last run nearly failed.
- [ ] Do not tighten `max_drift_g` on one sample. Repeat the run 2-3
      times once the link is fixed, then set it from the spread.

### `move_to_weigh` "stalled" was the amp refusing: POT over-travel is active

Second run of `demo_weigh_at_position.yaml` failed at `move_to_weigh`:
`linear rail did not reach its target (stalled); it stopped at
0.592 mm`. **Not the RS485 fault** — the three failing iterations logged
no link error at all.

- [x] **The rail did not move at all, three times in a row.** Commanded
      +49.408 mm at speed 25 on each iteration; position stayed at
      **exactly** 0.592 mm. Not "moved a little" — zero.
- [x] **The amp is healthy**: `Pr0.01` mode 1, `Pr3.04` speed 0,
      `Pr3.00` 1, feedback drift 0 pulses over 2 s, identity reads fine.
- [x] **The input frame says why.** `0b00101101`, stable across 5 reads:

      | SI | function | contact | input | meaning |
      |---|---|---|---|---|
      | SI1 | NOT (negative over-travel) | b | 1 | negative allowed |
      | SI2 | **POT (positive over-travel)** | **b** | **0** | **positive INHIBITED** |
      | SI6 | SRV-ON | a | 1 | servo on |

      POT is a **b-contact**, so it is *active when the input reads 0*.
      The amp is refusing positive motion, which matches the evidence
      exactly: the previous run's `move_home` (-49.486 mm) worked, and
      every positive move since has done nothing.
- [ ] **Bench check, most likely first.** The rail sits at 0.592 mm,
      near the origin, so a *position*-tripped positive limit makes no
      sense. A b-contact limit reads 0 when its circuit is **open** —
      indistinguishable from being pressed. Check the POT wiring on the
      X4 connector (SI2) before anything else; the amp was rewired
      repeatedly this evening. Then check whether the switch is
      physically pressed, and whether the -1.797 mm excursion past the
      origin disturbed something at the far end.
- [ ] **Fix the error message.** "stalled" cost an hour of looking in
      the wrong place. The amp already knew the answer and nobody asked
      it: when a move ends with zero displacement, read the input frame
      and name POT/NOT/SRV-ON in the error instead of guessing at
      oscillation. Belongs in `LinearMotorController.move_to_mm`.

### 2026-07-28 (same day, later) — cell1 gantry verified on hardware

The udev rule was installed by the operator; all three adapters came up
under pyftdi and `preflight.py` went to 3/3, clean.

- [x] **L1 verified on the real gantry.** `diagnose` → all three motors
      answered (`stage.ok` derived, not asserted); `/v1/pump/*` → 409
      "configured without a pump"; `/v1/linear/*` → 409 (wrong shape);
      `status` → live encoder values.
- [x] **All four motion types run, measured, and returned to origin:**

      | move | commanded | measured |
      |---|---|---|
      | `gantry/home` | 0 | X 0.0012, Z 0.0 (5.5 s) |
      | X out | 50 | 50.038 |
      | X back | 0 | 0.021 |
      | Z down | 50 | 50.021 |
      | Z up | 0 | 0.037 |

      Worst residual **0.038 mm**; a follow-up `status` read showed
      exactly 50.000, i.e. the servo closes the rest after the move
      returns. Worst Z-pair spread **0.020 mm**, and **0.0000 mm** at
      50 mm depth.
- [x] **RESOLVED: X's homing convention is correct as configured.**
      `x_coord_invert = true` with `home_dir_x = 0x00` drove X *away*
      from its limit and reached 50 mm exactly, so the cell's convention
      holds on this bench and the open question from the earlier entry is
      closed. `bridge.py` remains the odd one out (`0x00` with no invert)
      and still looks untested for absolute X moves — not ours to fix,
      but do not copy its X constants.
- [x] **Tolerances set from measurement, no longer placeholders.**
      `ARRIVAL_TOLERANCE_MM` and `Z_DESYNC_LIMIT_MM` 1.0 → **0.5 mm**
      (~13x the worst residual, ~25x the worst spread), and the
      scenario's `tolerance_mm` with them — the three must move together
      or the cell and the scenario disagree about what "arrived" means.
- [x] **Fixed a schema defect the dry run could not catch:**
      `GantryMoveRequest.accel_pct` was `ge=1`, rejecting `accel_pct: 0`
      with a 422 — but 0 is the MKS "no ramp" setting that both
      `bridge.py` and the validated `CVMeasure.py` use. Now `ge=0`; the
      identical defect in cell5's `ZStageMoveRequest` fixed too.
      LearnedPatterns #25.
- [x] L2 → L1 confirmed: `python -m orchestrator validate` against the
      **live** cell1 server, 23 steps ok.
- [x] Tests 66 passed, ruff clean.

- [ ] **`demo_gantry_step.yaml` has not been run end-to-end yet.** The
      operator is running it in `--step-mode` from their own terminal
      (this session has no stdin, so the engine's `input()` gate cannot
      be answered from here). Read the runlog under `runs/` afterwards
      and record the per-step timings.
- [ ] Extending past one 50 mm step per axis still needs the frame's real
      travel measured, especially below Z. Raise `max_mm` and add a
      step/verify pair per waypoint only after that.
- [ ] The cell1 server currently runs from a shell, not systemd. Enable
      `cell@nuc1-cell1` once the scenario has passed once.

### CORRECTION: POT was not the cause — Pr5.04 disables over-travel entirely

The entry above is wrong and the bench action it recommended (check the
POT wiring on X4/SI2) is a waste of time. `POT(SI2)=0` is real, but:

    Pr5.04 over-travel input setup = 1   (= inputs DISABLED / ignored)

The amp does not look at POT or NOT at all, so their state cannot inhibit
anything. I read the input frame, found an input that looked wrong, and
stopped before asking whether the amp was configured to care.

- [x] **The command path is healthy**, measured with the server stopped:
      execution rights 20/20, `Pr3.04 <- 0` writes **30/30**, feedback
      reads **30/30**. So the move command reaches the amp.
- [x] **All motion parameters are correct**: Pr0.01=1 (speed mode),
      Pr3.00=1 (internal speed), Pr3.04=0 at rest, SRV-ON input = 1.
- [x] `move_relative` **discards the speed write's return value**
      (LinearMotorController.py:776). That is a real defect and stays on
      the list, but it is not this failure: the write is landing.
- [ ] **So the amp accepts everything and does not drive.** That points
      at the amp's own state rather than at the link or the software.
      Two checks that need no protocol knowledge:
      1. **Read the front-panel display.** It shows the alarm code
         directly. An alarm disables the servo while leaving serial
         parameter access working — exactly what is observed.
      2. **Try to move the rail by hand.** Free = the servo is not
         energised, whatever the SRV-ON *input* reads. Held = energised,
         and the problem is the speed command itself.
- [ ] Probing `cmd=2 mode=0` returned `[01 52 00]` and `cmd=0 mode=2`
      returned `[10 16 00]`. These look like alarm/status registers, but
      the byte layout is not documented in this repo — do not read
      meaning into them without `MinasA6_driver_main.pdf`. Decoding them
      properly would make this diagnosable over the wire instead of by
      eye.

### 2026-07-28 (later) — `demo_gantry_stair.yaml` + the dry run learns to read bounds

- [x] **`scenarios/demo_gantry_stair.yaml`** — origin → (50,50) →
      (100,100) → (150,150) → origin, the L2 form of the driver's own
      `CVMeasure.py` stair. Asserts each 50 mm increment on **both** axes
      against the previous waypoint's readings, cross-checks every
      waypoint against `/v1/status`, bounds the far corner absolutely so
      three good steps cannot drift the origin, and closes the loop by
      comparing the parked position against the homed one. 31 steps, dry
      run ok.
      **The path is not diagonal**: `move_gantry` runs up → X → down
      whenever X changes, so Z fully retracts between every waypoint and
      the head never traverses X while lowered.
- [x] **The dry run now checks numeric bounds**, not just field names and
      types (`_check_bounds`). This is the gap that let `accel_pct: 0`
      pass validation twice and then 422 on the bench — the bound was in
      the OpenAPI document the validator had already fetched and parsed.
      Verified against the exact regression, plus valid bodies and both
      boundary values. LearnedPatterns #25.
- [x] All five scenarios re-validated under the stricter check; the four
      for cells on this bench pass (cell5's is `cell_unreachable`, as
      expected — Cell D is on NUC2).
- [x] Tests 71 passed, ruff clean.

- [ ] **`demo_gantry_stair.yaml` NEEDS A TRAVEL DECISION BEFORE IT RUNS.**
      It goes to 150 mm on both axes. The earlier run was deliberately
      capped at **50 mm** because the clearance below Z was not measured,
      and nothing on this bench has moved past 50 mm. The driver's
      `_max_travel_mm = 400` is a software clamp, not a measurement.
      Confirm 150 mm of free travel on X **and** below Z before running.
- [ ] `demo_gantry_step.yaml` still has not been run end-to-end either;
      run it first, it is the smaller of the two.

### THE ACTUAL CAUSE: Err16.0 overload — the rail was homing into its hard stop

Tracked as issue #15. Fixed in dbc87b0 (`home_mm`, default 5.0) and
driver 0e78c42; the operator actions below are still open.

Operator read the amp's front panel: **Err16.0**, motor overload
protection. That closes the chain and supersedes both earlier guesses in
this file (POT, then "the amp's own state, cause unknown").

    successful run's move_home overshot to -1.797 mm, past the origin
      -> rail pressed against the mechanical stop and kept pushing
      -> Err16.0 overload trips, servo de-energises
      -> every later move displaces exactly 0, in any direction
      -> serial parameter reads/writes keep working throughout

Everything measured fits: writes 30/30, all parameters correct, and
`SRV-ON input = 1` — which says a signal is present on the wire, not
that the servo is energised. An alarmed amp answers serial normally.

- [x] Confirmed the driver has **no alarm read and no alarm clear at
      all** (`grep alarm LinearMotorController.py` -> nothing). This is
      why `diagnose()` still answered `stage.ok: true`: model and
      version read fine, and nothing ever asks whether the amp is
      alarmed. Same "health check that cannot fail" as the hardcoded
      `ok: True` fixed in ca048f9, one level deeper.
- [ ] **Operator, in this order**: pull the rail clear of the stop by
      hand (the servo is off, so it moves freely), *then* clear the
      alarm — panel, A-CLR on SI8, or an amp power cycle. Clearing while
      still pressed re-trips it immediately.
- [ ] **Stop homing into the stop.** The origin at 0 mm sits on the
      mechanical stop and the closed loop coasts 1.5-1.8 mm past its
      target, so `move_home` *drives into it every time*. The -1.797 mm
      landing recorded above as "0.2 mm of margin left" was not a near
      miss on an assert — it was the rail pushing the stop. Fix by
      homing to a safe offset (~+3 mm) or redefining the origin; the
      value needs the measured clearance to the stop, so ask the
      operator for it rather than guessing.
- [ ] **Teach the driver about alarms.** Add an alarm read, surface it
      in `diagnose()` so `stage.ok` goes false when the amp is alarmed,
      and name it in the move error instead of "stalled". Today's
      sequence — stalled -> POT -> "amp state unknown" -> operator reads
      the panel — was three wrong turns for something the amp knew and
      could have said. `cmd=2 mode=0` returned `[01 52 00]`; decode it
      against `MinasA6_driver_sub.pdf` P.7-28~7-41 rather than guessing.

### Balance settle budget raised 30 s -> 60 s (operator request)

`confirm_zero` failed at exactly 30.003 s, hitting `SETTLE_TIMEOUT_S`.

- [x] `SETTLE_TIMEOUT_S` 30 -> 60 s, and the three `balance/weight` step
      timeouts 40/60/60 -> **75 s**, keeping the invariant that the
      driver's timeout fires first so its message ("is COM.OUTP set to
      AUTO.W/O?") reaches the operator instead of a bare "step was slow".
- [x] **Measured the balance while doing it, and it is healthy**:
      2.6 lines/s, consecutive-3 spread median 0.0005 g, p90 0.0009 g,
      and 21/23 windows inside the 0.002 g tolerance. A healthy settle
      costs ~1.2 s.
- [ ] So 30 s was never a tight budget — hitting it means the stream
      went **silent**, and 60 s of silence still fails, just later. The
      raise buys margin, not a fix. If it recurs, the question is why
      the balance stopped talking (COM.OUTP reverted? menu? standby?),
      not how long to wait.

## cell4 bring-up: closing summary (2026-07-28)

The original request was "L1 and L2 working on cell4, verified by the
scenario". Both scenarios now complete on real hardware. Recorded here so
the *verification*, not just the outcome, survives.

### What was delivered

| | Verified by |
|---|---|
| L1 serves cell4 (balance + MINAS A6 rail) over `/v1` | `diagnose` reads `BCE224I-1SKR` / `SerNo. 0047304196` and `MDDLN45SL Ver.1.016` off the wire |
| L2 runs a scenario across it | run `20260728T111725Z-demo_weigh_at_position`, **15/15**, `state: completed` |
| cell4's design premise — the balance can be carried and still weigh | 50 mm out and back moved a 25.7 g vial's reading by **0.0039 g** |
| One operator pause, not fifteen | `pause:` on the `weigh` step; run without `--step-mode` stops exactly twice |
| Measurements visible while the run happens | terminal prints each step's result and each assert's substituted expression |

### Defects this bring-up found, each fixed and re-measured

1. **Pre-flight passed a bench that could not start** — auto-detect and
   file permissions were never checked (LP #10).
2. **Three driver methods the cell called had never existed** in 66
   commits (LP #12) — the mock the cell was tested against was written
   by the same hand as the cell.
3. **`/v1/health` 500'd on a healthy cell** — `diagnose()` read every
   device twice, and the second read of a pair returned `None` (LP #14).
4. **The cell manufactured success from failed reads** — `status()`,
   `home_linear()` and `move_linear()` each substituted the value its
   own assert compared against (LP #15). A run recorded `home -> 0.0`
   passing `0.0 <= 0.1` with the rail at 0.39 mm.
5. **10% per-read RS485 failure = 100% inside a closed loop** (LP #16).
6. **`move_to_mm` returned the same type for "arrived" and "gave up"**
   (LP #17).
7. **The stop write failed 2 times in 30 and nobody checked it**
   (LP #18) — the one write whose loss leaves an axis moving.
8. **`diagnose()` hardcoded `ok: True`** — answered "healthy, safe to
   start" for an amp returning `None` to every read (ca048f9).
9. **A failed speed write was reported as "stalled"** (driver 0e78c42),
   which is what sent a day of diagnosis to the limit switches.
10. **Homing drove the rail into its mechanical stop** until the amp
    tripped Err16.0 (LP #27, issue #15) — and the assert that should
    have caught it passed, every run, at `-1.797` inside `±2.0`.

### Wrong turns, kept on the record

- Recommended **replacing the USB adapter** repeatedly across a full
  day. It was never the adapter: first a root port, then conducted EMI
  from the servo amp through the RS485 pair (LP #20).
- Shortened a timeout to "10x the median" and **halved** the success
  rate; reverted after measuring (LP #19).
- Blamed my own read-retry for an overshoot, then measured and found it
  made no difference (LP #18).
- Diagnosed **POT over-travel** as the cause of the dead rail and sent
  the operator to check X4 wiring. `Pr5.04 = 1` disables those inputs
  entirely (LP #27).
- **Refused to add link reconnection twice** on the grounds that it
  would mask a fault. Right for motion, wrong for reads; a 20-line
  change would have made the bench usable hours earlier (LP #26).

### How each hardware claim was measured

- **RS485 link**: 150 s soak, 959/959 positions, median 30 ms, across 18
  kernel re-enumerations and 14 driver reconnects.
- **Balance**: 2.6 lines/s stream, consecutive-3 spread median 0.0005 g,
  p90 0.0009 g, 21/23 windows inside the 0.002 g settle tolerance.
- **Amp command path**: execution rights 20/20, `Pr3.04` writes 30/30,
  feedback reads 30/30 — which is how "the amp accepts and does not
  drive" was established before the alarm was known.
- **EMI source**: amp off → 0 re-enumerations / 40 s; amp on → 15;
  amp on with the RS485 cable unplugged → 0. Three conditions, one
  conclusion.
- **Software**: ruff clean; 71 tests here, 16 in `LinearMotorController`
  — none of which touch hardware, which is exactly why every claim above
  is a bench measurement and not a test result.

### Deferred by decision, not by omission (operator, 2026-07-28)

- [x] **Issue #13 (RS485/EMI) — deferred to a later bench session.** The
      fix is electrical (SG between the ends, 120R termination once per
      end, shield grounded at one end only) and software cannot close
      it. What software does do is bounded and deliberate: reads
      reconnect, moves abort across a drop rather than resume. A run can
      still fail partway on a link drop; re-running it is the answer,
      and that abort is the rule working. Recorded in README under
      "Two known faults deliberately left open".
- [x] **Issue #15 (amp alarm read) — deferred deliberately.** Building
      it means *reproducing* an alarm, and the only alarm this bench
      knows how to raise is Err16.0: drive the rail into its stop until
      the motor overloads. Not worth doing repeatedly to a servo for a
      better error message. The operator carries it instead — **moves
      having no effect while `/v1/diagnose` still answers normally means
      read the front panel**, since software reports `stage.ok: true`
      throughout. In README, same section. If it is built later, decode
      the response against the manual rather than by provoking the amp.

### Still open

- The two items above are open in the tracker on purpose; "open" is the
  accurate state for deferred work, so neither issue was closed.
- `max_drift_g` (0.05) is still a guess against a single 0.0039 g
  measurement. Repeat the run 2–3 times and set it from the spread.
- Confirm the real clearance from 5 mm to the stop; raise `home_mm` if
  it is not comfortably more than the 1.8 mm coast.
- `home` has still never been proven to *move* anything: it starts
  inside tolerance, so `move_to_mm` returns `already_in_tolerance`
  without commanding motion. A scenario that homes from a known
  non-zero position is needed before homing is trusted.

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
      (`_resolve_lamp`; LearnedPatterns #29).
- [x] Added the `wait_s` scenario step (local timed hold, abort-sliced,
      not hazard-gated) to `orchestrator/scenario.py` + `engine.py`;
      spec §8.1 updated; 8 new tests.
- [x] Three Cell D bench scenarios: `demo_cell_d_lamp_blink.yaml`
      (3 blinks ≈ 5 s), `demo_cell_d_hotplate_30c.yaml` (30 °C, 10 s
      soak), `demo_cell_d_z_cycles.yaml` (home + 3 top-to-bottom
      strokes). All validate + run against FakeL1 (40/40 tests pass).
- [x] Real NUC2 configs written (gitignored): `server/nuc2/cell5.toml`,
      `orchestrator/config.toml` (cell5 @ 127.0.0.1:17062).
- [x] Python env bootstrapped in `.venv/` (LearnedPatterns #28).
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
      (readback granularity + control band; LearnedPatterns #32). Rerun
      COMPLETED 13/13 steps: lamp on -> 40 C -> heater+lamp off, final
      state safe. Runlog runs/20260728T093205Z-cell_d_lamp_heat_40c.
- [x] LearnedPatterns #31 (USB-powered interface + hub root cause,
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
- [x] Rename "Cell D" -> "Cell 5" repo-wide per the user's correction:
      docs (README, CLAUDE.md, spec, audit, bringup, deploy), code
      comments/docstrings, configs, and the scenario files
      (demo_cell_d_* -> demo_cell5_*, name: cell_d_* -> cell5_*).
      Historical ToDo/LearnedPatterns entries left as written
      (append-only). All 6 scenarios re-validated against the live L1;
      45/45 tests; ruff clean.
- [x] README rewritten for readers new to this kind of automation:
      layered mermaid overview, bench topology with real IPs, the
      verification ladder, a run sequence diagram, and a scenario
      mini-tutorial. (English per CommonClaude language rule.)
- [x] README: per-cell composition table added (cell1-5 -- NUC/port,
      shape class, devices with driver names, bench status, and the
      per-cell special properties).
- [x] Both PRs merged on the user's approval:
      InnoCORESDL PR #9 (feat/cell-d-bench-bringup -> main, 6 commits)
      and HotplateController PR #8 (fix/serial-robustness -> main,
      d2c4b3d). Merged branches deleted local+remote; submodule left on
      the pinned 2f3b8d6 (an ancestor of upstream main, identical
      content, so no pin-bump commit is needed).

## 2026-07-28 — Device repos updated with today's bench work

- [x] HotplateController: PR #10 merged (docs/nuc2-bench-notes) — the
      direct-USB-port rule, the full-drain wedge recovery, and the
      whole-degree readback tolerance recorded in README Troubleshooting
      + a bench-findings section; ToDo entry for the #8 verification.
      (Serial hardening itself merged earlier as PR #8.)
- [x] SmartPlugController: PR #15 merged (chore/nuc2-bench-2026-07-28) —
      device_list.md plug1 corrected to 192.168.0.237, LearnedPatterns
      §E4 (DHCP-drift rule), ToDo entry for the bench verification.
- [ ] ESP32S3BOX3MotorController: push DENIED (repo belongs to
      kkhyunhho; coport-uni has no write access). The intended ToDo
      entry (single-motor group path bench-verified; CAN silent after
      adapter USB power loss until the motor driver is re-powered) is
      recorded here instead — ask kkhyunhho for access or a fork to
      land it upstream.
- [x] Submodule pins bumped: HotplateController -> 1c2bc8b,
      SmartPlugController -> 00ef6c6.
- [x] ESP32S3BOX3MotorController forked to coport-uni (upstream write
      access denied): fork PR #2 merged with the Cell 5 bench ToDo
      entry (issue #1); submodule URL repointed to the fork in
      .gitmodules + SUBMODULES.md; pin bumped 872df98 -> f5c8089.

## 2026-07-28 — cell1 XZ gantry: bench-verified, 4/4 runs

Closes the bring-up opened earlier today. cell1 now runs its XZ gantry
through the full L1 + L2 stack, **without its syringe pump**.

### Result

| Run | Scenario | Steps | Result |
|---|---|---|---|
| `20260728T115546Z` | `demo_gantry_step` | 23 (8 calls + 15 asserts) | ✅ completed |
| `20260728T115738Z` | `demo_gantry_stair` | 31 (10 calls + 21 asserts) | ✅ completed |
| `20260728T120806Z` | `demo_gantry_stair` | 31 | ✅ completed |
| `20260728T120935Z` | `demo_gantry_stair` | 31 | ✅ completed |

**116 steps, zero failures.** The stair ran three times deliberately — one
run cannot tell you whether a tolerance is right (see "the correction").

### Measured (all from `runs/*/vars.json`, not estimated)

| Quantity | Value |
|---|---|
| Homing (Z pair, then X) | 5.64–5.74 s |
| X residual after homing | **0.0006 mm, identical across all 3 stair runs** |
| Move to (50,50) / (100,100) / (150,150) | 3.4 s / 5.2 s / 7.1 s |
| Return 150→origin | 7.05 s |
| Worst residual from target (20 waypoints) | **0.145 mm** |
| Worst 50 mm increment error | **0.094 mm** |
| Worst paired-Z spread | **0.020 mm** (0.003 mm typical, incl. at 150 mm depth) |
| X parked position after return | consistently **−0.10 mm** (past the origin) |

Move duration scales with travel, as it must: each stair waypoint retracts
Z fully before traversing X, so the frame covers more ground each step.

### The correction that matters

`ARRIVAL_TOLERANCE_MM` was first justified as "worst residual 0.038 mm,
~13x margin" — measured from **one** manual 50 mm move. Twenty waypoints
across four runs put the worst at **0.145 mm**, so the real margin is
**~3.5x**, not 13x. The tolerance value (0.5 mm) still stands and did not
change; the reasoning behind it was wrong by four times. Corrected in
`cell/pump_gantry_cell.py` and both scenarios. LearnedPatterns #33.

### Wrong turns, in the order they happened

1. **Documented X adapter serial did not exist on this bench.**
   `NTAM63XD` → `NTAMU6TO`. `open_xz` raises on a missing serial, so the
   server would have died at startup. The two Z serials were correct,
   which is exactly what one swapped adapter looks like. #22
2. **`preflight.py` reported the adapters as "not attached"** while
   `lsusb` showed all three. A permission failure (`ftdi_sio` bound +
   root-only `/dev/bus/usb`), not a missing device — it reads FTDI
   through libusb while the pump and balance go through pyserial, and
   only the libusb path was blocked. #23
3. **The gantry reported the position it was *asked* for.** `move_gantry`
   returned the commanded target and `diagnose()` hardcoded
   `stage.ok = True`, so an unpowered gantry answered `200 OK` with
   `x_mm: 50.0`. Found by reading the driver, not from a failure:
   `MKSMotor.move_to` *prints* `[ERROR]` and returns instead of raising,
   and `move_sync` discards the return value. #24
4. **The dry run passed twice, then the first real move was a 422.**
   `accel_pct: 0` against a wrong `ge=1` — and 0 is what both upstream
   reference scripts use. The bound was in the OpenAPI document the
   validator had already parsed; nothing looked at it. Fixed the schema
   **and** taught the validator to read bounds. #25
5. **The bounds checker's own first version flagged every valid field.**
   `(10).__lt__(1.0)` returns `NotImplemented`, which is truthy. Caught
   only by running it against a body known to be good. #25

### Resolved

- **X's homing direction.** Three conventions disagreed
  (`CVMeasure.py` / `bridge.py` / this cell). This cell's
  `home_dir_x=0x00` + `x_coord_invert=true` is correct on this bench —
  X reached 50 mm rather than driving into its limit. `bridge.py` is the
  odd one out and looks untested for absolute X moves.
- **Travel.** 150 mm on both axes is proven on this frame. It is *not*
  the frame's limit, which still nobody has measured.

### Still open

- [ ] **X parks ~0.10 mm past the origin** (negative) on every return,
      very repeatably, then `/v1/status` reads it back at +0.04 mm. Inside
      tolerance and the limit switch is right there, so it is harmless
      today. Worth understanding before anything depends on X = 0 exactly.
- [ ] **The pump is still off cell1.** Restore the `[pump]` table
      (`server/nuc1/cell1.toml.example` shows it) once its USB link is
      trusted; its flapping is a separate fault, chased in its own entry.
- [ ] **Frame travel beyond 150 mm is unmeasured.** The driver's
      `_max_travel_mm = 400` is a software clamp, not a measurement.
- [ ] **cell2 / cell3 are the same shape and still unrun.** They should
      need only their own adapter serials — which, per #22, must be read
      off the bus rather than copied from any doc.
- [x] GitHub issue (CommonClaude §4) — **issue #14** already covered this
      bring-up (opened earlier today, before the runs), so no duplicate was
      filed. Commented with the 4-run verification, the resolved X homing
      direction, the `_check_bounds` follow-up (which supersedes the
      issue's own "the dry run cannot catch this" claim) and the tolerance
      correction, then **closed it** — matching #10's precedent for cell4.
      The remaining bullets above are follow-ups, not part of the bring-up.

## 2026-07-29 — cell1 pump under L1/L2 YAML control (issue #21)

- [x] `scenarios/demo_pump_cycle.yaml`: pump init + 10 aspirate→dispense
      cycles valve 3 → 1 (1 unrolled + asserted leg by leg, 9 via
      `pump/cycle`), final `/v1/status` witness. Header documents the
      M05 gotcha: ports 3/1 are 180° apart = the SAME fluid state, so
      this validates the control path, not a fluid-path change.
- [x] `claude_test/conftest.py`: fake L1 gained `/v1/pump/initialize` +
      `/v1/pump/cycle` (schemas mirrored from `server/schemas.py`, held
      honest by the OpenAPI drift guard), a `/v1/diagnose` responder,
      and stateful valve/plunger answers.
- [x] `claude_test/test_orchestrator.py`: scenario validates, runs to
      completion (call sequence + 1+9=10 asserted), aborts before
      `pump/cycle` on an injected aspirate fault.
- [x] Dry-run against the live cell1: `demo_pump_cycle: ok (19 steps)`.
- [ ] **Hardware run blocked**: the SY-01B CH340 re-enumerates every
      ~10-15 s untouched (LearnedPatterns #35), so `[pump]` stays out of
      `server/nuc1/cell1.toml`. Fix cable/port/power, watch the devnum
      hold still, restore the table (block is in the config header),
      then `python -m orchestrator run scenarios/demo_pump_cycle.yaml
      --step-mode` with a vessel under the tip.

## 2026-07-29 — pump link stabilized; scenario flipped to 1 → 3 (issue #21)

- [x] After the bench's servo-driver power was tidied, the CH340 held one
      devnum (017) across a 60 s untouched watch AND six /v1/status
      valve queries — the ~10-15 s flapping of LearnedPatterns #35 is
      gone. `[pump]` restored in `server/nuc1/cell1.toml`; live
      diagnose: fw 8.33, 24.0 V, ok=true.
- [x] `demo_pump_cycle.yaml` ports swapped per request: source 1,
      dispense 3 (same fluid state either way — M05 gotcha unchanged).
      Dry-run against live cell1: ok (19 steps); pytest 87 passed.
- [x] Hardware run: needs the operator at the console for the
      first-motion gate —
      `.venv/bin/python -m orchestrator run scenarios/demo_pump_cycle.yaml`
      → **completed 2026-07-29**, run `20260729T103557Z-demo_pump_cycle`,
      19/19 steps, ~53 s, link held (devnum 017 throughout). Record:
      `claude_test/smoke_cell1_pump_20260729.md`.
