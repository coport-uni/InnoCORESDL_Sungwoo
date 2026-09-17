# Phase-0 Lua probe — fr5_a (192.168.0.58)

- UTC: 2026-08-13 07:34:17Z
- Read-only: no `Mode()`, no `ProgramLoad`, no `ProgramRun`, no motion.
- **7/7 probes answered.**

## Probes

### raw GetLuaList()

*which Lua programs exist, and in what return shape*

```
[0, 9, 'test.lua;example.lua;new_pr.lua;SimpleLoadIdentify.lua;Test0902.lua;Test1.lua;test2.lua;demo1.lua;Task1.lua;']
```

### raw GetLoadedProgram()

*the program the controller currently has loaded, if any*

```
[0, '/fruser/Task1.lua']
```

### raw GetProgramState()

*the state code a polling loop would read (1/2/3)*

```
[0, 1]
```

### wrapper GetProgramState()

*expected to fail or to return the dead 20004 struct (LearnedPatterns #40); recorded as the counter-evidence*

```
(0, <Field type=c_ubyte, ofs=6, size=1>)
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
[0, -103.6308787128713, -51.93182916924505, 48.8989499535891, -86.76675626547029, -90.45990292388613, 0.001957959467821782]
```

## Lua programs on this controller

- `/fruser/test.lua`
- `/fruser/example.lua`
- `/fruser/new_pr.lua`
- `/fruser/SimpleLoadIdentify.lua`
- `/fruser/Test0902.lua`
- `/fruser/Test1.lua`
- `/fruser/test2.lua`
- `/fruser/demo1.lua`
- `/fruser/Task1.lua`

## Program-state encoding (for reference)

- `1` — stopped / no program
- `2` — running
- `3` — paused
