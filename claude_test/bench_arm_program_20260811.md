# Bench record — `arm/program` on cell6 (fr5_a, 192.168.0.58)

- UTC: 2026-08-11
- Program: `/fruser/Test1.lua` (taught through the WebApp; chosen by the
  operator as the acceptance program)
- Operator present at the bench with the e-stop throughout.
- Spec: `docs/SPEC_ARM_LUA_PROGRAM.md` §7 T0' and T2.

This is the record for the **second** arm motion path — a Lua job program
executed by the controller — not for replay. It is worth reading for the
two defects it caught rather than for the pass at the end.

## T0' — read-only reconnaissance (no motion)

`python claude_test/probe_arm_lua.py --ip 192.168.0.58 --robot-id fr5_a`,
7/7 answered. Full output in
`claude_test/probe_arm_lua_fr5_a_20260811T042141Z.md`.

| Call | Raw return |
|---|---|
| `GetLuaList()` | `[0, 7, 'test.lua;example.lua;new_pr.lua;SimpleLoadIdentify.lua;Test0902.lua;Test1.lua;test2.lua;']` |
| `GetLoadedProgram()` | `[0, '/fruser/test2.lua']` |
| `GetProgramState()` raw | `[0, 1]` |
| `GetProgramState()` wrapper | `(0, 1)` |
| `GetCurrentLine()` | `[0, 0]` |
| `GetRobotErrorCode()` | `[0, 0, 0]` |

Three assumptions settled, one **disproved**:

- raw `GetProgramState()` really is `(error, state)`.
- `GetLuaList()` really is `(error, count, "a;b;c;")`, trailing `;` and all.
- The program is **`Test1.lua`, capital T**. There is no lowercase
  `test1.lua` on this arm, and `ProgramLoad` takes the name literally —
  the cell's allow-list rejected `test1.lua` with a 400, correctly.
- **Disproved**: the spec draft claimed the SDK's `GetProgramState()`
  wrapper would raise `TypeError` here, reasoning from LearnedPatterns
  #40. It did not — this controller's port-20004 stream is alive. The
  wrapper is still unusable, because it reads `robot_state` (a different
  field) and hard-codes a success code, but for the reason measured
  rather than the one guessed.

## Rejection gates over HTTP (no motion)

| Body `name` | Result |
|---|---|
| `test1.lua` | 400 — not in `allowed_programs` (case-sensitive) |
| `../../etc/passwd` | 400 — path separator |
| `Test1.txt` | 400 — wrong extension |
| `sub/Test1.lua` | 400 — path separator |

## T2 attempt 1 — the fabricated success

```
POST /v1/arm/program {"name": "Test1.lua"}
{"completed":true,"name":"Test1.lua","elapsed_s":0.00283,
 "last_line":0,"joints_deg":[-137.7548,-99.8768,95.2654,…]}
HTTP 200  0.017s
```

Read on its own this looks like a clean no-op. It was not: the operator
watched the arm move immediately afterwards, and a later pose read showed

```
pre-run  : [-137.755,  -99.877, 95.266, -87.986, -89.388, 2.915]
after    : [ -46.485, -102.933, 95.298, -81.581, -89.910, 2.918]
max delta: 91.27 deg   (joint 1)
```

The 200 was sent 2.8 ms into a motion that ran for 23.6 s. **Without the
operator's eye this would have been recorded as a pass.**

## Diagnosis — measured, not guessed

`scratchpad/measure_start.py`, raw XMLRPC, 50 ms polling:

```
run      -> 0
  t+  0.001s  state=1  line=0
  t+  0.160s  state=2  line=4
  t+  5.643s  state=2  line=9
  t+ 11.064s  state=2  line=11
  t+ 11.116s  state=2  line=12
  t+ 14.332s  state=2  line=14
  t+ 14.384s  state=2  line=15
  t+ 17.388s  state=2  line=18
  t+ 23.712s  state=2  line=0
  t+ 23.764s  state=1  line=0
  -> started after 160 ms, ran 23.604 s
joints after : [-46.485, -102.932, 95.294, -81.583, -89.909, 2.921]
fault        : [0, 0, 0]
```

Two independent defects:

1. **`ProgramRun` answers on acceptance, 160 ms before the state enters
   "running".** Polling inside that window reads the pre-start idle as
   "finished".
2. **`GetCurrentLine` resets to 0 as the program ends** (…15, 18, then
   0), so reporting the last reading always yields `last_line: 0`.

Note the program returns to its own start pose — final joints are within
0.004° of initial — so a pose delta could not have served as the
evidence either. `last_line` is the only signal that the script ran.

Written up as LearnedPatterns #46.

## T2 attempt 2 — after the fix

`_confirm_started()` (poll until the controller leaves the stopped state,
5 s grace against 160 ms measured) and a high-water mark for `last_line`:

```
POST /v1/arm/program {"name": "Test1.lua"}
{"completed":true,"name":"Test1.lua","elapsed_s":23.7887,"last_line":18,
 "joints_deg":[-46.4839,-102.9325,95.2952,-81.5825,-89.9091,2.9219]}
HTTP 200  wall 23.803s
```

and afterwards:

```
GET /v1/status
joints      : [-46.484, -102.933, 95.295, -81.582, -89.909, 2.922]
busy        : False
last_program: {'name': 'Test1.lua', 'outcome': 'completed',
               'last_line': 18, 'elapsed_s': 23.7887}
```

`elapsed_s` 23.789 s agrees with the independently measured 23.604 s, and
`last_line` 18 agrees with the highest line observed. The HTTP response
now arrives when the program ends rather than 2.8 ms in.

## cell7 (fr5_b, 192.168.0.59) — the same path on the second arm

Run after the fix, so cell7 never saw the broken version. The operator
created `Cell7Test1.lua` on that controller for this test; a read-only
`GetLuaList()` beforehand returned
`[0, 2, 'test0403.lua;Cell7Test1.lua;']`, and `allowed_programs` was
narrowed to the one program being commissioned.

```
POST /v1/arm/enable   -> fault_cleared: True, error_settled: [0, 0]
POST /v1/arm/program {"name": "Cell7Test1.lua"}
{"completed":true,"name":"Cell7Test1.lua","elapsed_s":25.2984,
 "last_line":13,
 "joints_deg":[-48.7082,-99.9236,97.2845,-92.4631,-88.1204,-2.1137]}
HTTP 200  wall 25.316s
```

```
GET /v1/status
joints      : [-48.708, -99.923, 97.285, -92.463, -88.120, -2.114]
busy        : False
last_program: {'name': 'Cell7Test1.lua', 'outcome': 'completed',
               'last_line': 13, 'elapsed_s': 25.2984}
```

Both arms therefore run a controller-side job program through `/v1`:

| | cell6 / fr5_a | cell7 / fr5_b |
|---|---|---|
| Program | `Test1.lua` | `Cell7Test1.lua` |
| `elapsed_s` | 23.789 | 25.298 |
| HTTP wall | 23.803 s | 25.316 s |
| `last_line` | 18 | 13 |
| Fault after | none | none |

Like cell6's, this program returns to its own start pose (final joints
within 0.002° of initial), so `last_line` is again the only evidence the
script executed rather than the pose.

## T2 attempt 3 — after the replay path was deleted

The runs above were made while `cell/arm_replay_cell.py` still carried
both motion paths. The replay half was then removed and the module
renamed to `cell/arm_cell.py` / `ArmCell` (LearnedPatterns #47), which is
a refactor of a motion path and therefore unverified by the tests that
survived it. Both servers were restarted on the new module and both
programs re-run.

Surface after the restart — the removed routes are gone and `diagnose`
no longer carries a `replay` block:

```
arm routes: ['/v1/arm/enable', '/v1/arm/jog_joint', '/v1/arm/program']
cell6  robot: fr5_a  ready: True   program: {"running": false, …,
       "dir": "/fruser", "allowed": ["Test1.lua"]}
cell7  robot: fr5_b  ready: True   program: {…, "allowed": ["Cell7Test1.lua"]}
```

| | cell6 / `Test1.lua` | cell7 / `Cell7Test1.lua` |
|---|---|---|
| `elapsed_s` | 23.786 | 25.293 |
| HTTP wall | 23.798 s | 25.307 s |
| `last_line` | 18 | 13 |
| `busy` after | False | False |
| Fault | none | none |

Both agree with the pre-refactor numbers to ~10 ms (23.789 / 25.298), so
the removal changed the surface and not the behaviour. Rejection gates
re-checked on cell6 with no motion: `test1.lua` (case), `../etc/passwd`
(traversal) and `Test1.txt` (extension) all 400.
`python -m orchestrator validate scenarios/demo_arm_program.yaml` → ok,
10 steps.

## What these runs do NOT establish

- **`POST /v1/stop` against a running program is unmeasured** (spec §7
  T2-2). The three-stage stop is unit-tested only; GAP-9 is not disproved
  for this path until it is timed on the bench.
- **The arm did not "arrive" anywhere provable.** `completed: true` means
  the controller returned to state 1 with no latched fault and the
  encoder answered — the cell never reads the script, so it has no
  expected end pose (spec §6.4).
- `scenarios/demo_arm_program.yaml` has not been run end to end; the
  orchestrator's operator gate wants a console confirmation, so that run
  belongs to the operator.
