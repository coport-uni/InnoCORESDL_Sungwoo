# Adding a new cell

This repo is the SDLClaude **reference implementation** of a cell. To bring a
new hardware cell onto the same `/v1` web + server, copy the patterns here.
For the *why* (Level/Phase/cell terminology, the recursive HTTP substrate,
port-allocation rule) see SDLClaude `ARCHITECTURE.md`; this doc is the *how*.

A "cell" = the devices that must be **coordinated to move together** (the cell
boundary rule). Each cell is one process: a FastAPI `/v1` server wrapping one
`Cell` implementation that drives the devices.

## Steps

### 1. Add your driver as a submodule
Add the device's L0 driver repo under `external/` — same as
`external/SyringePumpController/`, `external/MKSServo57DCANController/`, etc.:

```bash
git submodule add https://github.com/coport-uni/<Repo> external/<Repo>
```

Never make local-only edits inside `external/` — commit upstream, push,
then bump the pin. Give the repo a `pyproject.toml` so it installs as a
package, add it to `requirements.txt` as `-e ./external/<Repo>`, and
describe it in `external/SUBMODULES.md`. Anything the driver is missing
(e.g. VID:PID port resolution) belongs upstream, not in a local shim.

### 2. Write `cell/<your>_cell.py`
Copy `cell/pump_gantry_cell.py` (or `balance_linear_cell.py`) as a template
and implement the `Cell` protocol (`cell/cell_protocol.py`). The methods fall
into **two layers** (see SDLClaude `ARCHITECTURE.md` → "Cell contract"):

| Layer | Group | Methods |
|---|---|---|
| **Substrate** (universal) | Discovery | `diagnose() -> dict`, `status() -> dict` |
| **Substrate** (universal) | Lifecycle | `stop()`, `close()`, classmethod `open(config)` |
| **Action set** (per family) | Balance | `tare()`, `read_weight()`, `set_ambient(level)` |
| **Action set** (per family) | Pump | `initialize()`, `move_valve()`, `aspirate()`, `dispense()`, `cycle()` |
| **Action set** (per family) | Gantry | `home_gantry()`, `move_gantry(x, z, *, speed_pct, accel_pct)` |
| **Action set** (per family) | Linear | `home_linear()`, `move_linear(y_mm)` |
| **Action set** (per family) | ZStage | `home_zstage()`, `move_zstage(z_mm, *, speed_pct, accel_pct)` |
| **Action set** (per family) | Hotplate | `read_hotplate()`, `set_hotplate_temperature(c)`, `set_hotplate_heater(*, enabled)`, `set_hotplate_speed(rpm)`, `set_hotplate_stirrer(*, enabled)` |
| **Action set** (per family) | Lamp | `read_lamp()`, `set_lamp(*, enabled)` |

Rules:
- **Always implement the Substrate** (discovery + lifecycle) — that's what the
  orchestrator/web use for every cell.
- **Implement the action sets your hardware has;** for a device family this
  cell does NOT have, `raise WrongStateError(...)` (see the defensive stubs
  `PumpGantryCell._no_balance` / `BalanceLinearCell._no_pump`). The web greys
  those out from `diagnose()` presence flags.
- **New motion/product family → new action set, don't overload an existing
  one.** `gantry` (XZ, CAN via `mks_motor`) and `linear` (Y, RS-485 via `lmc`)
  are separate action sets — different motors, axes, and wire protocols, so a
  shared "stage" signature would lean toward one and misfit the other. A
  genuinely different product gets **its own** action set + `/v1` routes,
  conforming to the Substrate only — never crammed into pump/balance/gantry/
  linear.
- **A robot arm is just another action family — it does NOT break the cell
  format.** The planned arm is operated by hardcoded trajectories triggered as
  discrete named actions (e.g. `run_trajectory("A"|"B"|"C")`, surfaced as A/B/C
  buttons in the web), not a continuous pose/grip interface. That is still a
  normal cell: it implements the same Substrate (health/diagnose/status,
  lifecycle, error envelope, one `/v1` server on its own port) and simply adds
  an `arm` action set with its own routes. The Substrate is what makes it
  compose with every other cell; the discrete-trajectory action set is the only
  part that's arm-specific.
- `open(config)` is the **composition root**: open the drivers, run any
  one-time setup, return the instance. Hold drivers as attributes (`has-a`);
  translate name/unit/order in the method bodies (Adapter pattern).
- Intra-cell imports are relative (`from .cell_protocol import ...`); driver
  imports are absolute, by *package* name (`from sy01b import ...`) — except
  the two path-imported repos (`external.HotplateController...`,
  `external.SmartPlugController...`), which are not packaged yet.
- **Name fields so a YAML scenario can address them.** L2 scenarios are
  YAML, and YAML 1.1 turns a bare `on:` key into a boolean — so a field
  named `on` is unreachable. Use `enabled` (see `LearnedPatterns.md` #8);
  the same goes for `off`, `yes`, `no`, `y`, `n`.
- **If the cell can heat or energize something, `stop()` must kill that
  too**, not just motion. `PumpZThermalCell.stop()` is the reference: it
  attempts motor, pump, heater, stirrer and lamp, and reports whatever did
  not stop instead of giving up at the first failure.

**Worked example — Cell 5 (cell5).** Four devices in one cell (pump +
single Z + hotplate + IR lamp), three brand-new action sets, added in one
pass: `cell/pump_z_thermal_cell.py`, the `zstage`/`hotplate`/`lamp` routes
and schemas, `_load_pump_z_thermal()` + `--cell pump_z_thermal`,
`server/nuc2/cell5.toml.example`. Note what did *not* change: the L2
orchestrator, which picked the new routes up from the cell's OpenAPI.

### 3. Raise the right `CellError` — it maps to HTTP automatically
`server/errors.py` maps each subclass to a status code, so just raise the
correct one and the web gets a stable error envelope:

| Exception | HTTP | When |
|---|---|---|
| `InvalidArgError` | 400 | bad argument (out of range, unknown level) |
| `WrongStateError` | 409 | not initialized / device absent / wrong order |
| `DeviceFaultError` | 500 | hardware fault (overload, init failure) |
| `TransportError` | 503 | serial/CAN link down |
| `CellTimeoutError` | 504 | device didn't respond in time |

### 4. Add a config + loader
- Define a `@dataclass(frozen=True, slots=True)` config (ports, serials) in
  your cell module, like `Config` / `BalanceLinearConfig`.
- Add a `_load_<shape>()` in `server/__main__.py` that parses the TOML tables
  into that config (mirror `_load` / `_load_balance_linear`).
- Add a `server/cell<N>.toml.example` (real `.toml` is gitignored). Resolve
  device addresses by **VID:PID**, not `/dev/ttyUSBn` (renumbers).

### 5. Wire the `--cell` flag
In `server/__main__.py`: add your shape to the `--cell` `choices`, and a
branch that calls your `_load_<shape>()` + `YourCell.open(cfg)` as the
factory passed to `create_app`.

### 6. Assign a port
Per the SDLClaude port table, one port per `/v1` server (cell1=17054,
cell2=17056, …). Put it in your `[server] port`.

### 7. Lint, then bring up at the bench
Verification is hardware-in-the-loop — there is no in-memory fake. Lint
first, then bring the cell up against the real devices with an operator
ready and an e-stop handy (see the safety rules in `CLAUDE.md`):
```bash
ruff check cell/ server/
python -m server --config server/cellN.toml
# then GET /v1/health, GET /v1/diagnose before any motion command
```

### 8. Register in the web (when the UI comes back)
`web/` was removed from the repo along with the other pre-L2 work; the
React operator UI is M7 in `docs/L2_ORCHESTRATOR_SPEC.md` and its last
state is in git history. When it returns, adding a cell is a registry
entry (base URL) in the web's cell list, not a new site. Until then a cell
is reachable through its own `/v1` API and through the L2 orchestrator,
which discovers it from `orchestrator/config.toml`.

## Checklist
- [ ] driver submodule in `external/<Repo>/` (installable) + `SUBMODULES.md`
      + `-e` line in `requirements.txt`
- [ ] `cell/<your>_cell.py` implements all `Cell` methods (absent → raise)
- [ ] correct `CellError` subclasses raised
- [ ] config dataclass + `_load_<shape>()` + `cell<N>.toml.example`
- [ ] `--cell` choice + factory branch in `server/__main__.py`
- [ ] port assigned
- [ ] `ruff check cell/ server/` passes; cell brought up at the bench (health + diagnose)
- [ ] cell registered in the orchestrator's `config.toml` (and in the web's cell list once M7 lands)
