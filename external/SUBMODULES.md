# External submodules

Every hardware driver lives in its own upstream repository, tracked here
as a **git submodule** under `external/` (replacing the earlier
copied-in-repo "vendored" layout). Their runtime deps (`pyserial`,
`pyftdi`, …) stay in `requirements.txt`.

Clone / refresh with:

```bash
git submodule update --init --recursive
```

## Device drivers

The four the cells depend on are **installable packages**, listed in
`requirements.txt` as editable installs (`-e ./external/<Repo>`), so a
`git submodule update` takes effect without reinstalling:

| Path | Upstream | Import as | Device |
|---|---|---|---|
| `SyringePumpController/` | coport-uni/SyringePumpController | `sy01b` | SY-01B syringe pump. |
| `PrecisionScaleController/` | coport-uni/PrecisionScaleController | `entris_ii` | Entris-II BCE224I balance (SBI). |
| `ESP32S3BOX3MotorController/` | coport-uni/ESP32S3BOX3MotorController (fork of kkhyunhho/ESP32S3BOX3MotorController) | `mks_motor` | MKS SERVO57D XZ gantry over FTDI USB2CAN (pyftdi). Carries the paired-Z interlock (`move_sync`, `home_xz`, `stop_group_hard`) and the limit-quirk handling `PumpGantryCell` requires. Forked 2026-07-28 so bench notes can land (no write access upstream); sync from upstream when kkhyunhho advances. |
| `LinearMotorController/` | coport-uni/LinearMotorController | `LinearMotorController` | MINAS A6 linear rail, RS485 standard protocol (`Pr5.37=0`) with the `PIDController` loop; `resolve_port` accepts a `"VID:PID"` string. |

Not installed as packages — imported by path or not yet used:

| Path | Upstream | Notes |
|---|---|---|
| `HotplateController/` | coport-uni/HotplateController | IKA RCT digital; imported as `external.HotplateController.hotplate_controller`. |
| `SmartPlugController/` | coport-uni/SmartPlugController | Tapo P110M; imported as `external.SmartPlugController.smartplugcontroller`. |
| `MKSServo57DCANController/` | coport-uni/MKSServo57DCANController | A second, `ftd2xx`-based MKS driver (561 lines) without the group-interlock API. Kept for reference; the gantry runs on `ESP32S3BOX3MotorController` instead. |
| `FR5Controller/` | coport-uni/FR5Controller | FR5 robot arm. Its `fairino/` SDK is a Cython extension needing `setup.py build_ext`, so it is not packaged yet; no cell imports it. |

## Conventions

| Path | Upstream | Purpose |
|---|---|---|
| `CommonClaude/` | coport-uni/CommonClaude | shared ruleset; source of `.claude/` hooks + `settings.json`. |

## FR5ControllerVLA — pinned, deliberately NOT installed

| Path | Upstream | Purpose |
|---|---|---|
| `FR5ControllerVLA/` | coport-uni/FR5ControllerVLA | huggingface/lerobot fork carrying the `fairino_follower` robot and the `lerobot-replay` CLI that cell6/cell7 drive. |

Two exceptions to the usual submodule rules apply here, both from
`docs/SPEC_ARM_REPLAY_CELL.md`:

- **D4 — pinned but not editable-installed.** There is no
  `-e ./external/FR5ControllerVLA` line in `requirements.txt` and there
  must not be. The submodule exists to *pin the version* of the replay
  code; it is not imported by anything in `cell/` or `server/`.
- **D3 — it runs in its own conda env.** `ArmReplayCell` shells out to
  `lerobot-replay` under the `[arm] conda_env` named in the cell's TOML
  (default `lerobot`). lerobot pulls in torch; the SDL venv must not.
  The subprocess boundary is also what makes the cell's `stop()` real —
  killing a process stops the ServoJ stream in a way no in-process flag
  could.

The arm's *read* path is separate and does not involve this repo: the
cell talks to the controller through `FR5Controller/fairino`, over
XMLRPC on port 20003. Note that the vendored SDK's convenience wrappers
are unusable on this firmware — see `LearnedPatterns.md` #40 and #41 —
so `cell/arm_replay_cell.py` calls the XMLRPC methods directly.

## Updating a driver

```bash
git -C external/<Repo> fetch origin && git -C external/<Repo> checkout <ref>
git add external/<Repo> && git commit -m "chore: bump <Repo> to <ref>"
```

`git submodule status` is the authority on which commit each submodule
is pinned to — the tables above deliberately do not duplicate it.

Never make local-only edits inside `external/`: commit the change to
the upstream repo, push it, then bump the pin here. The ruff post-write
hook skips `external/` for that reason.
