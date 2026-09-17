# Phase-0 Lua probe — fr5_b (192.168.0.59)

- UTC: 2026-09-15 14:59:42Z
- Read-only: no `Mode()`, no `ProgramLoad`, no `ProgramRun`, no motion.
- **7/7 probes answered.**

## Probes

### raw GetLuaList()

*which Lua programs exist, and in what return shape*

```
[0, 9, 'test0403.lua;Cell7Test1.lua;pick_and_place_vial.lua;zensor.lua;demoA.lua;demob.lua;ZensorWash.lua;pick_and_place_head.lua;init_head.lua;']
```

### raw GetLoadedProgram()

*the program the controller currently has loaded, if any*

```
[0, '/fruser/init_head.lua']
```

### raw GetProgramState()

*the state code a polling loop would read (1/2/3)*

```
[0, 1]
```

### wrapper GetProgramState()

*expected to fail or to return the dead 20004 struct (LearnedPatterns #40); recorded as the counter-evidence*

```
(0, 1)
```

### raw GetCurrentLine()

*the progress channel — the only feedback a running Lua program offers*

```
[0, 0]
```

### raw GetRobotErrorCode()

*main/sub fault codes; a latched fault blocks everything*

```
[0, 0, 0]
```

### raw GetActualJointPosDegree(1)

*proves the session is live and the encoder answers*

```
[0, -42.30084409808168, -122.6879157642326, 120.7569326268564, -90.27759514232673, -90.29282371596534, -2.112638265779703]
```

## Lua programs on this controller

- `/fruser/test0403.lua`
- `/fruser/Cell7Test1.lua`
- `/fruser/pick_and_place_vial.lua`
- `/fruser/zensor.lua`
- `/fruser/demoA.lua`
- `/fruser/demob.lua`
- `/fruser/ZensorWash.lua`
- `/fruser/pick_and_place_head.lua`
- `/fruser/init_head.lua`

## Program-state encoding (for reference)

- `1` — stopped / no program
- `2` — running
- `3` — paused
