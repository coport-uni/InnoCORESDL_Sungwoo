# InnoCORE $H_2$ Project Scenario Description


## Overview

Scenario `.yaml` 파일들에 대한 설명.
전체적인 구조, Device 구동 low-level code 등에 대한 자세한 설명은 해당 repo 참조 바람
https://github.com/coport-uni/InnoCORESDL_Sungwoo

> 기준 시점: 2026-10-14 15:30 (벤치 체크아웃 `InnoCORESDL_Sungwoo`, upstream `main` 570edd8 + 미커밋 작업 트리). 이 폴더의 YAML 6개(1014 계열 4 + 보조 2)의 스텝 수·params·run 기록을 실제 파일과 대조해 작성함. `init_1014` / `synthesis_1014` / `analysis_1014` 는 10-14 에 터미널에서 각각 1회 실행(아래 Output 은 그 터미널 로그).

Contents

1. init - 작동 전 cell(Gantry, Linear Rail 등의 장비) 원점 초기화 및 Hot plate 작동
2. Synthesis — 합성 단계
3. Analysis — 분석 단계
4. 통합 Process(Synthesis+Analysis)
5. 보조·검증 — 준비·측정 (2개)
6. 공통 `params`

장비 명칭

| cell | 장치 | NUC | 포트 |
|---|---|---|---|
| cell1 / cell2 / cell3 | SY-01B 시린지 펌프 + XZ 갠트리(X 1축, 동기 Z 2축). 합성 분주 | NUC1 / NUC2 / NUC2 | 17054 / 17056 / 17058 |
| cell4 | MINAS A6 리니어 레일 + Entris-II 저울. vial 을 싣고 스테이션 사이를 이동 | NUC1 | 17060 |
| cell5 | 분석 펌프 SY-01B + 볼스크류 단일 Z + IKA 핫플레이트 + 선풍기 플러그 | NUC2 | 17062 |
| cell6 | FR5 로봇팔 `fr5_a` (192.168.0.58). 합성: vial 배치 | NUC1 | 17064 |
| cell7 | FR5 로봇팔 `fr5_b` (192.168.0.59). 분석: vial·head 이송, zensor, 세척 | NUC1 | 17066 |

공통 사항

- **Directory**: 모든 YAML 은 `scenarios/shinyeong_test/` 에 있고, 명령은 **레포 루트**에서 실행한다. 아래 명령의 `$REPO` 는 NUC1 기준 `/home/innocorecontroller/workspace/InnoCOREServer/InnoCORESDL_Sungwoo`.

- **Run Command(Directory Terminal 기준)**: 모든 파일에 공통. 순서는 반드시 validate → `--step-mode` → free run.
```Terminal
cd $REPO
.venv/bin/python -m orchestrator validate scenarios/shinyeong_test/<file>.yaml              # 서버 OpenAPI 대조만, 장치 안 건드림
.venv/bin/python -m orchestrator run      scenarios/shinyeong_test/<file>.yaml --step-mode  # 스텝마다 콘솔 확인
.venv/bin/python -m orchestrator run      scenarios/shinyeong_test/<file>.yaml              # 첫 모션만 확인 후 끝까지
```
- **Input**:
	- args: `--step-mode` (스텝별 확인), `--param KEY=VALUE` (params 덮어쓰기, 반복 가능), `--config <toml>` (주소록, 기본 `orchestrator/config.toml`)
	- param: 각 YAML 의 `params` 블록. 값은 §6 표. 파일마다 쓰는 키만 아래에 적는다.
	- 전제(모든 파일): 그 파일이 쓰는 cell 서버가 모두 떠 있고 `orchestrator/config.toml` 에 주석 해제돼 있을 것. 첫 모션 스텝에서 콘솔에 `!! next step '<id>' moves hardware … [enter]=continue, a=abort >` 가 뜨며 **Enter** 를 쳐야 진행된다(`a` 는 중단, 우회 불가). cell5 는 NUC2 `InnoCORESDL_shinyeong` 체크아웃에서 `claude_test/test_cell5_server_shinyeong.py --config server/nuc2/cell5.toml --max-travel 450` 으로 띄운다(정식 `python -m server` 는 Z 400 mm 한계라 421 을 못 감; 포트 17062 는 한 프로세스만).

- **Output**: `runs/<UTC>-<name>/` (gitignore)
	- `meta.json` — `state`(completed / failed / aborted), 시작·종료 시각, `git_commit`, 그때의 주소록
	- `run.jsonl` — 스텝 한 줄씩: id, cell, action, 응답, `ok`, `error`, 소요 시간. **측정값은 여기**
	- `vars.json` — `save_as` 로 저장한 응답 전부
	- `scenario.yaml` — 실행 시점 사본(`--param` 반영)
	- 파일별 "Output" 에는 그 run 에서 추가로 남는 값만 적는다.

- **Process 순서**: 아래 각 파일에 스텝 id 와 함께 적는다. 모든 move 뒤에는 엔코더 readback 을 assert 로 비교하는 `verify_*` 스텝이 따라오며(200 OK 가 아니라 이 assert 가 "움직였다"의 증거), 그 verify 스텝은 생략한다.

- **안전(모든 파일 공통)**: 레일(cell4)은 소프트웨어로 멈추지 않고(GAP-1), 다른 cell 의 `POST /v1/stop` 도 진행 중 move 뒤에 큐된다(GAP-9). **물리 e-stop 을 든 사람이 있어야 한다.** e-stop 뒤에는 cell7 gripper 를 다시 활성화할 것(안 하면 lua 가 11/14행에서 끝남). 상세는 루트 README `### Safety gaps`.

- **명명 규칙 예외**: `init_1014` / `synthesis_1014` / `analysis_1014` / `1cycle_1014` 는 작업자 지정 이름으로 `test_…_shinyeong` 규칙의 예외. `1cycle_1014.yaml` 의 YAML `name` 은 스키마가 숫자 시작을 막아 `one_cycle_1014`(runlog 폴더명이 그 이름).



---

## Init

### `init_1014.yaml`

시나리오 실행 전 cell들의 원점 초기화 및 작동 테스트 단계
약 30s ~ 1min 정도가 소요
Hot plate 는 여기서 on 만 시킨다 — `analysis_1014.yaml` 끝(`cell5_heater_off`)에서 꺼지므로, 분석까지 가지 않을 때는 별도로 종료할 것:
`curl -s -X POST http://192.168.0.120:17062/v1/hotplate/heater -H 'Content-Type: application/json' -d '{"enabled": false}'`

- **Directory**: `scenarios/shinyeong_test/init_1014.yaml` (29 스텝 = `1cycle_1014` 의 1~4 단계)

- **Run Command**:
```Terminal
cd ~/workspace/InnoCOREServer/InnoCORESDL_Sungwoo
.venv/bin/python -m orchestrator validate scenarios/shinyeong_test/init_1014.yaml
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/init_1014.yaml
```
- **Input**:
	- args: 없음(첫 모션 게이트 `init_home_cell1` 에서 Enter)
	- param: `hotplate_target_c`(100), `init_force`(2), `rail_home_mm`(17.98), `rail_tol_mm`, `z_up_mm`/`gantry_tol_mm`(헤드 up 판정), `c5_z_tol_mm`. 나머지 키는 `1cycle_1014` 와 같지만 여기서는 쓰이지 않음
	- 전제: 7개 cell 서버 전부. 저울 위 vial 없음.

- **Output**: `d1..d7`, `h1/h2/h3`(갠트리 홈), `h5`(cell5 Z 홈), `p5init`, `hp_t`/`hp_on`, `railed`. 끝 상태: 갠트리 3개 0, cell5 Z 상단(0), 분석 펌프 plunger 0, 히터 on(100 °C 로 가열 시작), 레일 17.98 ± 2. 1 run `20261014T052721Z` completed(약 1 분).
  
```Terminal
init_1014: ok (29 steps)
run 20261014T052721Z-init_1014 started (29 steps)
  [ok ] diag_cell6  cell6/diagnose  0.007s
        pump={'present': False, 'ok': True}, balance={'present': False, 'ok': True}, stage={'present': False, 'ok': True}, ok_to_initialize=True, arm={'ok': True, 'robot_id': 'fr5_a', 'ip': '192.168.0.58', 'reachable': True, 'gripper': True, 'joints_deg': [-46.39798306002475, -119.2577883276609, 116.751165106745, -88.692518177599, -91.15867690284654, 0.09311185024752476], 'fault': [0, 0], 'ready': True, 'detail': None}, program={'running': False, 'name': None, 'last': {'name': 'Task1_original.lua', 'outcome': 'completed', 'last_line': 29, 'elapsed_s': 29.817440386861563}, 'dir': '/fruser', 'allowed': ['Test1.lua', 'Task1.lua', 'Task1_original.lua']}
  [ok ] diag_cell7  cell7/diagnose  0.007s
        pump={'present': False, 'ok': True}, balance={'present': False, 'ok': True}, stage={'present': False, 'ok': True}, ok_to_initialize=True, arm={'ok': True, 'robot_id': 'fr5_b', 'ip': '192.168.0.59', 'reachable': True, 'gripper': True, 'joints_deg': [-42.30084409808168, -122.6876982131807, 120.7571501779084, -90.27759514232673, -90.29282371596534, -2.112855816831683], 'fault': [0, 0], 'ready': True, 'detail': None}, program={'running': False, 'name': None, 'last': {'name': 'init_head.lua', 'outcome': 'completed', 'last_line': 29, 'elapsed_s': 14.233865869231522}, 'dir': '/fruser', 'allowed': ['Cell7Test1.lua', 'pick_and_place_vial.lua', 'pick_and_place_head.lua', 'zensor.lua', 'ZensorWash.lua', 'init_head.lua']}
  [ok ] diag_cell4  cell4/diagnose  0.07s
        pump={'present': False, 'ok': True}, balance={'model': 'N     -   6.6267 g', 'serial_number': 'SerNo.    0047304196', 'ok': True}, stage={'model': 'MDDLN45SL', 'version': 'Ver.1.016', 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell1  cell1/diagnose  0.709s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '32656', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTAMU6TO', 'x_mm': -0.0, 'z_a_mm': -0.0, 'z_b_mm': -0.0, 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell2  cell2/diagnose  0.716s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '50487', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTB19XKA', 'x_mm': -0.0, 'z_a_mm': -0.0, 'z_b_mm': -0.0, 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell3  cell3/diagnose  0.716s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '30308', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTB3FXCE', 'x_mm': -0.0, 'z_a_mm': -0.0, 'z_b_mm': -0.0, 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell5  cell5/diagnose  1.217s
        pump={'software_version': '8.33', 'serial_number': '30946', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'axes': 1, 'serial': 'NTB3EP5R', 'ok': True}, ok_to_initialize=True
  [ok ] verify_arm_ready  assert/assert  0.0s
        True == True and True == True and False == False
  [ok ] verify_arm7_ready  assert/assert  0.0s
        True == True and True == True and False == False
  [ok ] verify_rail_and_balance  assert/assert  0.0s
        True == True and True == True
  [ok ] verify_cell1_has_pump  assert/assert  0.0s
        True == True and True == True
  [ok ] verify_cell23_gantries  assert/assert  0.0s
        True == True and True == True and True == True and True == True
  [ok ] verify_cell5  assert/assert  0.0s
        True == True and True == True and True == True
  [ok ] verify_all_gantries_up  assert/assert  0.0s
        -0.0 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5

  !! next step 'init_home_cell1' moves hardware. Clear the frame, keep the e-stop in reach.
  [enter]=continue, a=abort > 
  [ok ] init_home_cell1  cell1/gantry/home  5.634s
        x_mm=0.0006103515625, z_mm=0.0
  [ok ] verify_init_cell1  assert/assert  0.0s
        0.0006103515625 <= 0.5 and 0.5 + 0.0006103515625 >= 0 and 0.0 <= 0.5
  [ok ] init_home_cell2  cell2/gantry/home  5.576s
        x_mm=0.0006103515625, z_mm=0.0
  [ok ] verify_init_cell2  assert/assert  0.0s
        0.0006103515625 <= 0.5 and 0.5 + 0.0006103515625 >= 0 and 0.0 <= 0.5
  [ok ] init_home_cell3  cell3/gantry/home  5.816s
        x_mm=0.0, z_mm=0.0
  [ok ] verify_init_cell3  assert/assert  0.0s
        0.0 <= 0.5 and 0.5 + 0.0 >= 0 and 0.0 <= 0.5
  [ok ] init_home_cell5  cell5/zstage/home  2.628s
        z_mm=0.0
  [ok ] verify_init_cell5  assert/assert  0.0s
        0.0 <= 0.5
  [ok ] cell5_pump_init  cell5/pump/initialize  11.212s
        valve=4, plunger_uL=0.0
  [ok ] verify_pump5_initialised  assert/assert  0.0s
        0.0 == 0
  [ok ] cell5_hotplate_target  cell5/hotplate/temperature  0.106s
        target_c=100.0
  [ok ] cell5_heater_on  cell5/hotplate/heater  0.702s
        heating=True, target_c=100.0
  [ok ] rail_home  cell4/linear/move  0.028s
        y_mm=18.64
  [ok ] verify_railed  assert/assert  0.0s
        18.64 - 17.98 <= 2.0 and 17.98 - 18.64 <= 2.0
  [ok ] rail_park_home  cell4/linear/move  0.028s
        y_mm=18.64

run 20261014T052721Z-init_1014: completed
runlog: runs/20261014T052721Z-init_1014
```

- **Process 순서**
1. `diag_cell6/7/4/1/2/3/5` + `verify_arm_ready`/`verify_arm7_ready`/`verify_rail_and_balance`/`verify_cell1_has_pump`/`verify_cell23_gantries`/`verify_cell5`/`verify_all_gantries_up`: 7개 cell 의 `diagnose` 를 읽어 팔 2개 ready·fault 없음, 저울 ok, cell1 펌프 있음, cell2/3 펌프·갠트리 ok, cell5 pump·stage ok, 헤드 3개가 올라가 있음(Z ≤ 3.5 mm). 하나라도 아니면 아무것도 움직이기 전에 중단.
2. `init_home_cell1` → `init_home_cell2` → `init_home_cell3`: 갠트리 3개를 차례로 홈(Z 먼저 스위치까지 올린 뒤 X). 첫 모션 게이트가 여기서 뜬다.
3. `init_home_cell5`: cell5 Z 를 상단 스위치로(영점 설정). `cell5_pump_init`: 분석 펌프 홈(`force` = `init_force`, plunger 0 — 시린지 잔량이 노즐로 나온다).
4. `cell5_hotplate_target`(`hotplate_target_c`) → `cell5_heater_on`: 핫플레이트 설정·히터 on. 온도 도달을 기다리지 않는다.
5. `rail_home` → `rail_park_home`: 레일을 `rail_home_mm` 로(서버 home_mm 5.0 이 아니라 명시적 `linear/move`).

---

## Synthesis

### `synthesis_1014.yaml`

합성 단계만 실행하는 시나리오 `.yaml`파일
약 10min 정도 소요(2 mL 분주 3회가 각 139 s)
`init_1014.yaml` 을 먼저 실행하고 이어서 실행할 것 — 프리플라이트 `verify_rail_home_pre` 가 레일이 홈(17.98 ± 2)에 있는지 확인하고 아니면 거부한다
원활한 무게 측정을 위해 반드시 vial을 Tare 후 준비 단계 모듈에 배치할 것
작동 중 Gantry system의 각 축의 ball-screw 모터들이 end-stop 스위치에 닿지 않을 경우 그대로 Process가 정지됨. 해당 오류의 경우 문제가 발생한 ball-screw 모터의 케이블을 분리 후 물리적으로 end-stop에 위치시킨 뒤 재연결 하는 것으로 해결 가능
z축의 경우 좌우 balance가 안맞는 문제가 종종 발생. 해당 오류의 경우 문제의 Gantry system 축들을 서로 분리 후 오류 축 ball-screw 모터의 커버를 분리 뒤 고정 나사를 충분히 조이는 것으로 해결 가능
58번 로봇팔의 경우 로봇팔과 그리퍼 모두 교시 모드(파란색 LED 점등)가 켜져 있어야 작동 가능
3회 정도 실행 후 용매가 부족한 경우가 있으므로, 여분을 충분히 준비할 것(각 펌프 공급액 2 mL/run 이상, vial ≥ 6 mL)

- **Directory**: `scenarios/shinyeong_test/synthesis_1014.yaml` (59 스텝 = 프리플라이트 11 + `1cycle_1014` 의 5~7 단계)

- **Run Command**:
```Terminal
cd ~/workspace/InnoCOREServer/InnoCORESDL_Sungwoo
.venv/bin/python -m orchestrator validate scenarios/shinyeong_test/synthesis_1014.yaml
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/synthesis_1014.yaml
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/synthesis_1014.yaml --param dispense_cycles=5   # 500 µL/cell (호스 채우기·짧은 확인)
```
- **Input**:
	- args: 없음(첫 모션 게이트 `arm_enable` 에서 Enter). 양을 바꿀 땐 `--param dispense_cycles=N`
	- param: `program`(Task1_original.lua), `arm_last_line`/`arm_min_elapsed_s`, `rail_home_mm`/`rail_cell1/2/3_mm`/`rail_end_mm`/`rail_tol_mm`, `c1/2/3_x_mm`, `c1/2/3_z_mm`(223), `volume_uL`(100) × `dispense_cycles`(20), `source_port`/`dispense_port`/`init_force`, `z_up_mm`/`gantry_tol_mm`, `speed_pct`/`accel_pct`
	- 전제: cell1, 2, 3, 4, 6 서버. 레일 홈, 헤드 3개 up, 저울 비어 있음(`init_1014` 직후 상태). 핫플레이트는 건드리지 않는다.

- **Output**: `prog`(cell6 `completed`/`last_line`/`elapsed_s`), `rail1/2/3`, `disp/disp2/disp3`(`cycles_done` == 20), `railend`, `c3home`. 끝 상태: 레일 978 ± 2 에 **머문 채**, vial 이 저울 위(3 × 2 mL), cell1/2 는 X 250·Z 3, cell3 는 X 0·Z 3. 1 run `20261014T053114Z` completed(약 10 분).
  
```Terminal
synthesis_1014: ok (59 steps)
run 20261014T053114Z-synthesis_1014 started (59 steps)
  [ok ] diag_cell6  cell6/diagnose  0.007s
        pump={'present': False, 'ok': True}, balance={'present': False, 'ok': True}, stage={'present': False, 'ok': True}, ok_to_initialize=True, arm={'ok': True, 'robot_id': 'fr5_a', 'ip': '192.168.0.58', 'reachable': True, 'gripper': True, 'joints_deg': [-46.39798306002475, -119.2580058787128, 116.751382657797, -88.69273572865097, -91.15867690284654, 0.09289429919554454], 'fault': [0, 0], 'ready': True, 'detail': None}, program={'running': False, 'name': None, 'last': {'name': 'Task1_original.lua', 'outcome': 'completed', 'last_line': 29, 'elapsed_s': 29.817440386861563}, 'dir': '/fruser', 'allowed': ['Test1.lua', 'Task1.lua', 'Task1_original.lua']}
  [ok ] diag_cell4  cell4/diagnose  0.074s
        pump={'present': False, 'ok': True}, balance={'model': 'N     -   6.6270', 'serial_number': 'SerNo.    0047304196', 'ok': True}, stage={'model': 'MDDLN45SL', 'version': 'Ver.1.016', 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell1  cell1/diagnose  0.709s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '32656', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTAMU6TO', 'x_mm': -0.0, 'z_a_mm': -0.0, 'z_b_mm': -0.0, 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell2  cell2/diagnose  0.716s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '50487', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTB19XKA', 'x_mm': -0.0, 'z_a_mm': -0.0, 'z_b_mm': 0.0006103515625, 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell3  cell3/diagnose  0.715s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '30308', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTB3FXCE', 'x_mm': -0.0, 'z_a_mm': -0.0, 'z_b_mm': -0.0, 'ok': True}, ok_to_initialize=True
  [ok ] verify_arm_ready  assert/assert  0.0s
        True == True and True == True and False == False
  [ok ] verify_rail_and_balance  assert/assert  0.0s
        True == True and True == True
  [ok ] verify_cell1_has_pump  assert/assert  0.0s
        True == True and True == True
  [ok ] verify_cell23_gantries  assert/assert  0.0s
        True == True and True == True and True == True and True == True
  [ok ] verify_all_gantries_up  assert/assert  0.0s
        -0.0 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5 and 0.0006103515625 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5 and -0.0 <= 3.0 + 0.5
  [ok ] pre_rail_home  cell4/status  0.028s
        weight_g=0.0, valve=-, plunger_uL=0.0, stage_x_mm=18.64, stage_z_mm=0.0, busy=False
  [ok ] verify_rail_home_pre  assert/assert  0.0s
        18.64 - 17.98 <= 2.0 and 17.98 - 18.64 <= 2.0

  !! next step 'arm_enable' moves hardware. Clear the frame, keep the e-stop in reach.
  [enter]=continue, a=abort > 
  [ok ] arm_enable  cell6/arm/enable  2.519s
        error_before=[0, 0], error_after=[0, 0], error_settled=[0, 0], fault_cleared=True, joints_deg=[-46.39798306002475, -119.2577883276609, 116.751382657797, -88.69273572865097, -91.15867690284654, 0.09311185024752476]
  [ok ] verify_enabled  assert/assert  0.0s
        True == True
  [ok ] arm_program  cell6/arm/program  29.835s
        completed=True, name=Task1_original.lua, elapsed_s=29.822033767122775, last_line=29, joints_deg=[-46.39341448793317, -119.2638797571163, 116.7596495977723, -88.69317083075495, -91.15867690284654, 0.09289429919554454]
  [ok ] verify_arm_done  assert/assert  0.0s
        True == True and 29 >= 29 and 29.822033767122775 >= 20.0
  [ok ] rail_at_cell1  cell4/linear/move  21.12s
        y_mm=491.288
  [ok ] verify_rail_at_cell1  assert/assert  0.0s
        491.288 - 491.752 <= 2.0 and 491.752 - 491.288 <= 2.0
  [ok ] cell1_pump_init  cell1/pump/initialize  6.425s
        valve=4, plunger_uL=0.0
  [ok ] verify_pump_initialised  assert/assert  0.0s
        0.0 == 0
  [ok ] cell1_x_over_vial  cell1/gantry/move  6.439s
        x_mm=249.9932861328125, z_mm=3.055419921875
  [ok ] verify_cell1_over_vial  assert/assert  0.0s
        249.9932861328125 - 249.985 <= 0.5 and 249.985 - 249.9932861328125 <= 0.5
  [ok ] cell1_descend  cell1/gantry/move  4.824s
        x_mm=249.9932861328125, z_mm=223.00689697265625
  [ok ] verify_cell1_descended  assert/assert  0.0s
        223.00689697265625 - 223.0 <= 0.5 and 223.0 - 223.00689697265625 <= 0.5
  [ok ] cell1_dispense  cell1/pump/cycle  139.081s
        cycles_done=20, final_valve=2
  [ok ] verify_dispensed  assert/assert  0.0s
        20 == 20
  [ok ] cell1_retract  cell1/gantry/move  4.823s
        x_mm=249.9932861328125, z_mm=2.9833984375
  [ok ] cell1_status  cell1/status  0.355s
        weight_g=0.0, valve=2, plunger_uL=0.0, stage_x_mm=249.9835205078125, stage_z_mm=3.00048828125, busy=False
  [ok ] verify_cell1_clear  assert/assert  0.0s
        3.00048828125 <= 3.0 + 0.5 and None == null
  [ok ] rail_at_cell2  cell4/linear/move  11.721s
        y_mm=729.554
  [ok ] verify_rail_at_cell2  assert/assert  0.0s
        729.554 - 729.246 <= 2.0 and 729.246 - 729.554 <= 2.0
  [ok ] cell2_pump_init  cell2/pump/initialize  10.208s
        valve=4, plunger_uL=0.0
  [ok ] verify_pump2_initialised  assert/assert  0.0s
        0.0 == 0
  [ok ] cell2_x_over_vial  cell2/gantry/move  6.376s
        x_mm=250.0262451171875, z_mm=3.05938720703125
  [ok ] verify_cell2_over_vial  assert/assert  0.0s
        250.0262451171875 - 250.004 <= 0.5 and 250.004 - 250.0262451171875 <= 0.5
  [ok ] cell2_descend  cell2/gantry/move  4.851s
        x_mm=250.0262451171875, z_mm=223.0133056640625
  [ok ] verify_cell2_descended  assert/assert  0.0s
        223.0133056640625 - 223.0 <= 0.5 and 223.0 - 223.0133056640625 <= 0.5
  [ok ] cell2_dispense  cell2/pump/cycle  139.511s
        cycles_done=20, final_valve=2
  [ok ] verify_dispensed2  assert/assert  0.0s
        20 == 20
  [ok ] cell2_retract  cell2/gantry/move  4.752s
        x_mm=250.0262451171875, z_mm=2.962646484375
  [ok ] cell2_status  cell2/status  0.361s
        weight_g=0.0, valve=2, plunger_uL=0.0, stage_x_mm=250.0030517578125, stage_z_mm=3.00750732421875, busy=False
  [ok ] verify_cell2_clear  assert/assert  0.0s
        3.00750732421875 <= 3.0 + 0.5 and None == null
  [ok ] rail_at_cell3  cell4/linear/move  10.894s
        y_mm=947.01
  [ok ] verify_rail_at_cell3  assert/assert  0.0s
        947.01 - 947.5 <= 2.0 and 947.5 - 947.01 <= 2.0
  [ok ] cell3_pump_init  cell3/pump/initialize  9.95s
        valve=4, plunger_uL=0.0
  [ok ] verify_pump3_initialised  assert/assert  0.0s
        0.0 == 0
  [ok ] cell3_x_over_vial  cell3/gantry/move  6.738s
        x_mm=243.0975341796875, z_mm=2.9913330078125
  [ok ] verify_cell3_over_vial  assert/assert  0.0s
        243.0975341796875 - 243.0 <= 0.5 and 243.0 - 243.0975341796875 <= 0.5
  [ok ] cell3_descend  cell3/gantry/move  5.091s
        x_mm=243.0975341796875, z_mm=222.9913330078125
  [ok ] verify_cell3_descended  assert/assert  0.0s
        222.9913330078125 - 223.0 <= 0.5 and 223.0 - 222.9913330078125 <= 0.5
  [ok ] cell3_dispense  cell3/pump/cycle  139.418s
        cycles_done=20, final_valve=2
  [ok ] verify_dispensed3  assert/assert  0.0s
        20 == 20
  [ok ] cell3_retract  cell3/gantry/move  4.758s
        x_mm=243.0975341796875, z_mm=2.94281005859375
  [ok ] cell3_status  cell3/status  0.361s
        weight_g=0.0, valve=2, plunger_uL=0.0, stage_x_mm=242.9962158203125, stage_z_mm=2.99041748046875, busy=False
  [ok ] verify_cell3_clear  assert/assert  0.0s
        2.99041748046875 <= 3.0 + 0.5 and None == null
  [ok ] rail_to_end  cell4/linear/move  3.406s
        y_mm=977.298
  [ok ] verify_rail_at_end  assert/assert  0.0s
        977.298 - 978.0 <= 2.0 and 978.0 - 977.298 <= 2.0
  [ok ] cell3_x_home  cell3/gantry/move  6.676s
        x_mm=-0.0091552734375, z_mm=2.99102783203125
  [ok ] verify_cell3_x_home  assert/assert  0.0s
        -0.0091552734375 <= 0.5 and 2.99102783203125 <= 3.0 + 0.5

run 20261014T053114Z-synthesis_1014: completed
runlog: runs/20261014T053114Z-synthesis_1014
```

- **Process 순서**
1. `diag_cell6/4/1/2/3` + `verify_arm_ready`/`verify_rail_and_balance`/`verify_cell1_has_pump`/`verify_cell23_gantries`/`verify_all_gantries_up`: 5개 cell 의 `diagnose` 를 읽어 팔 ready, 저울 ok, 펌프 3개 있음, 헤드 3개 up. `pre_rail_home` → `verify_rail_home_pre`: 레일이 `rail_home_mm` ± `rail_tol_mm` 인지. 아니면 거부.
2. `arm_enable` → `arm_program`: cell6 이 `Task1_original.lua` 를 실행해 vial 을 저울 위에 놓는다(`last_line` ≥ 29, ≥ 20 s 로 완료 판정). 첫 모션 게이트가 `arm_enable` 에서 뜬다.
3. cell N = 1, 2, 3 각각: `rail_at_cellN`(레일을 스테이션으로) → `cellN_pump_init`(펌프 홈, plunger 0) → `cellN_x_over_vial`(X 를 vial 위로, Z 3) → `cellN_descend`(Z 하강 223 mm) → `cellN_dispense`(`pump/cycle` `dispense_cycles` × `volume_uL` = 20 × 100 µL, 공급 포트 1 → 노즐 포트 2, 약 139 s) → `cellN_retract`(Z 3) → `cellN_status` 로 Z 가 올라왔는지 확인 — **이 확인이 다음 레일 이동의 전제**(헤드-레일 충돌 방지).
4. `rail_to_end`: 레일을 `rail_end_mm`(978) 로 → `cell3_x_home`: cell3 X 를 0 으로 파킹. 레일은 end 에 머문다.


---

## Analysis

분석 단계 및 종료 후 원점 복귀까지의 시나리오
약 5~6min 소요
Linear Rail이 합성 단계 종료 지점(978 ± 2)에 있지 않으면 프리플라이트 `verify_rail_at_end_pre` 가 거부하니, 해당 위치로 linear rail 작동 후 진행할 것
반드시 59번 로봇팔과 그리퍼 모두 교시 모드(파란색 LED)로 변경 후 진행할 것
cell7 에 펜던트에서 돌린 프로그램이 남아 있으면 `arm/program` 이 409 로 거부된다 — `curl -s -X POST http://127.0.0.1:17066/v1/stop -H 'Content-Type: application/json' -d '{}'` 로 정리

### `analysis_1014.yaml`

- **Directory**: `scenarios/shinyeong_test/analysis_1014.yaml` (40 스텝 = 프리플라이트 12 + `1cycle_1014` 의 8~13 단계).

- **Run Command**:
```Terminal
cd ~/workspace/InnoCOREServer/InnoCORESDL_Sungwoo
.venv/bin/python -m orchestrator validate scenarios/shinyeong_test/analysis_1014.yaml
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/analysis_1014.yaml
```

- **Input**:
	- args: 없음(첫 모션 게이트 `arm7_enable` 에서 Enter)
	- param: `analysis_program_{vial,head,zensor,wash,init_head}` 와 각 `_last_line`/`_min_s`, `c5_z_dispense_mm`(421), `c5_volume_uL`(50), `c5_z_tol_mm`, `rail_end_mm`/`rail_home_mm`/`rail_tol_mm`, `z_up_mm`/`gantry_tol_mm`, `speed_pct`/`accel_pct`
	- 전제: cell7, 4, 5 서버 + cell1/2/3 서버(확인·재홈용). 레일 978 ± 2, vial 이 저울 위, 헤드 3개 up(`synthesis_1014` 직후 상태). 히터는 `init_1014` 에서 켜진 것을 이 파일 끝에서 끈다.

- **Output**: `prog7v/h/z/w/i`, `disp5`, `c5z`/`c5home`, `fan_on`/`fan_off`, `hp_off`, `railback`, `h1end/h2end/h3end`. 끝 상태: 레일 17.98 ± 2, 갠트리 3개 0, cell5 Z 0, 팬·히터 off. 1 run `20261014T055909Z` **failed** — vial·head·zensor·cell5 50 µL·wash·팬 on·init_head 까지 정상, 종료 병렬 블록에서 `init_cell2_shutdown`(cell2 gantry/home) 이 180 s 타임아웃(cell2 Z_A 홈 스위치가 늦게 잡힘, 10-13 과 같은 증상; 홈 자체는 그 뒤 끝났음). 블록이 실패해 `cell5_fan_off`/`cell5_heater_off` 는 실행되지 않았지만, abort 의 `/v1/stop` 이 cell5 를 세우며 팬·히터를 껐고 벤치는 레일 18.2·갠트리 0 으로 끝났다. 재발 시 cell2 Z_A 스위치 마운트 점검.
```Terminal
analysis_1014: ok (40 steps)
run 20261014T055909Z-analysis_1014 started (40 steps)
  [ok ] diag_cell7  cell7/diagnose  0.007s
        pump={'present': False, 'ok': True}, balance={'present': False, 'ok': True}, stage={'present': False, 'ok': True}, ok_to_initialize=True, arm={'ok': True, 'robot_id': 'fr5_b', 'ip': '192.168.0.59', 'reachable': True, 'gripper': True, 'joints_deg': [-42.30084409808168, -122.6879157642326, 120.7567150758044, -90.27824779548267, -90.29282371596534, -2.112855816831683], 'fault': [0, 0], 'ready': True, 'detail': None}, program={'running': False, 'name': None, 'last': {'name': 'pick_and_place_vial.lua', 'outcome': 'completed', 'last_line': 11, 'elapsed_s': 4.181434962898493}, 'dir': '/fruser', 'allowed': ['Cell7Test1.lua', 'pick_and_place_vial.lua', 'pick_and_place_head.lua', 'zensor.lua', 'ZensorWash.lua', 'init_head.lua']}
  [ok ] diag_cell4  cell4/diagnose  0.075s
        pump={'present': False, 'ok': True}, balance={'model': 'Model  BCE224I-1SKR', 'serial_number': 'SerNo.    0047304196', 'ok': True}, stage={'model': 'MDDLN45SL', 'version': 'Ver.1.016', 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell5  cell5/diagnose  1.214s
        pump={'software_version': '8.33', 'serial_number': '30946', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'axes': 1, 'serial': 'NTB3EP5R', 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell1  cell1/diagnose  0.71s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '32656', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTAMU6TO', 'x_mm': 249.984130859375, 'z_a_mm': 2.9998779296875, 'z_b_mm': 2.9974365234375, 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell2  cell2/diagnose  0.716s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '50487', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTB19XKA', 'x_mm': 250.003662109375, 'z_a_mm': 3.004150390625, 'z_b_mm': 3.0084228515625, 'ok': True}, ok_to_initialize=True
  [ok ] diag_cell3  cell3/diagnose  0.719s
        pump={'present': True, 'software_version': '8.33', 'serial_number': '30308', 'config': '4 way|9600|100K|TSY|high|XLP|AUTO', 'supply_volts': 24.0, 'valve': '2', 'ok': True}, balance={'present': False, 'ok': True}, stage={'serial_x': 'NTB3FXCE', 'x_mm': -0.0, 'z_a_mm': 2.9815673828125, 'z_b_mm': 2.999267578125, 'ok': True}, ok_to_initialize=True
  [ok ] verify_arm7_ready  assert/assert  0.0s
        True == True and True == True and False == False
  [ok ] verify_rail_and_balance  assert/assert  0.0s
        True == True and True == True
  [ok ] verify_cell5  assert/assert  0.0s
        True == True and True == True and True == True
  [ok ] verify_all_gantries_up  assert/assert  0.0s
        2.9998779296875 <= 3.0 + 0.5 and 2.9974365234375 <= 3.0 + 0.5 and 3.004150390625 <= 3.0 + 0.5 and 3.0084228515625 <= 3.0 + 0.5 and 2.9815673828125 <= 3.0 + 0.5 and 2.999267578125 <= 3.0 + 0.5
  [ok ] pre_rail  cell4/status  0.028s
        weight_g=0.0, valve=-, plunger_uL=0.0, stage_x_mm=977.392, stage_z_mm=0.0, busy=False
  [ok ] verify_rail_at_end_pre  assert/assert  0.0s
        977.392 - 978.0 <= 2.0 and 978.0 - 977.392 <= 2.0

  !! next step 'arm7_enable' moves hardware. Clear the frame, keep the e-stop in reach.
  [enter]=continue, a=abort > 
  [ok ] arm7_enable  cell7/arm/enable  2.517s
        error_before=[0, 0], error_after=[0, 0], error_settled=[0, 0], fault_cleared=True, joints_deg=[-42.30084409808168, -122.6881333152846, 120.7569326268564, -90.2780302444307, -90.29282371596534, -2.112638265779703]
  [ok ] verify_arm7_enabled  assert/assert  0.0s
        True == True
  [ok ] arm7_pick_and_place_vial  cell7/arm/program  18.777s
        completed=True, name=pick_and_place_vial.lua, elapsed_s=18.762986254878342, last_line=25, joints_deg=[-133.9390035194926, -116.2862585086633, 141.7355981203589, -115.4260616491336, -89.99956489789604, -88.43863609993812]
  [ok ] verify_arm7_vial  assert/assert  0.0s
        True == True and 25 >= 25 and 18.762986254878342 >= 10.0
  [ok ] arm7_pick_and_place_head  cell7/arm/program  21.791s
        completed=True, name=pick_and_place_head.lua, elapsed_s=21.776698363013566, last_line=32, joints_deg=[-42.29518777073019, -122.6861753558168, 120.7584554842203, -90.2767249381188, -90.29260616491337, -2.112638265779703]
  [ok ] verify_arm7_head  assert/assert  0.0s
        True == True and 32 >= 31 and 21.776698363013566 >= 10.0
  [ok ] arm7_zensor  cell7/arm/program  19.782s
        completed=True, name=zensor.lua, elapsed_s=19.767061243765056, last_line=17, joints_deg=[-42.30802328279703, -122.6850876005569, 120.748883237933, -90.27280901918317, -90.29064820544555, -2.090230507425743]
  [ok ] verify_arm7_zensor  assert/assert  0.0s
        True == True and 17 >= 16 and 19.767061243765056 >= 10.0
  [ok ] cell5_z_dispense  cell5/zstage/move  8.697s
        z_mm=421.0
  [ok ] verify_cell5_at_dispense  assert/assert  0.0s
        421.0 - 421.0 <= 0.5 and 421.0 - 421.0 <= 0.5
  [ok ] cell5_dispense  cell5/pump/cycle  4.649s
        cycles_done=1, final_valve=2
  [ok ] verify_dispensed5  assert/assert  0.0s
        1 == 1
  [ok ] cell5_z_home  cell5/zstage/home  16.271s
        z_mm=0.0
  [ok ] verify_cell5_home  assert/assert  0.0s
        0.0 <= 0.5
  [ok ] arm7_zensor_wash  cell7/arm/program  109.785s
        completed=True, name=ZensorWash.lua, elapsed_s=109.76733626099303, last_line=62, joints_deg=[-42.308240833849, -122.6792137221534, 120.7578028310643, -90.27259146813118, -90.2899955522896, -2.095234181621287]
  [ok ] verify_arm7_wash  assert/assert  0.0s
        True == True and 62 >= 61 and 109.76733626099303 >= 55.0
  [ok ] cell5_fan_on  cell5/fan/switch  0.409s
        is_on=True, target=192.168.0.77, devices=['192.168.0.77']
  [ok ] verify_fan_on  assert/assert  0.0s
        True == True
  [ok ] arm7_init_head  cell7/arm/program  14.252s
        completed=True, name=init_head.lua, elapsed_s=14.236916339024901, last_line=29, joints_deg=[-42.2797416460396, -122.6809541305693, 120.7582379331683, -90.26845799814356, -90.29086575649752, -2.114813776299505]
  [ok ] verify_arm7_init_head  assert/assert  0.0s
        True == True and 29 >= 28 and 14.236916339024901 >= 10.0
  [ok ] init_cell3_shutdown  cell3/gantry/home  5.181s
        x_mm=0.0, z_mm=0.0
  [ok ] init_cell1_shutdown  cell1/gantry/home  13.258s
        x_mm=0.0006103515625, z_mm=0.0
  [ok ] rail_home_shutdown  cell4/linear/move  40.578s
        y_mm=18.226
### 해당 부분은 cell2 z축 좌측의 이물질이 끼어 원점 복귀 인식이 안됨. 그 외 정상적으로 종료를 확인
  [FAIL] init_cell2_shutdown  cell2/gantry/home  180.005s
        cell2 POST gantry/home timed out after 180.0s
 

run 20261014T055909Z-analysis_1014: failed
runlog: runs/20261014T055909Z-analysis_1014
```

- **Process 순서**
1. `diag_cell7/4/5/1/2/3` + `verify_arm7_ready`/`verify_rail_and_balance`/`verify_cell5`/`verify_all_gantries_up`: cell7 ready, 저울 ok, cell5 pump·stage ok, 헤드 3개 up. `pre_rail` → `verify_rail_at_end_pre`: 레일이 `rail_end_mm` ± `rail_tol_mm` 인지. 아니면 거부.
2. `arm7_enable` → `arm7_pick_and_place_vial`: cell7 이 저울 위 vial 을 분석 홀더로(≥ 25 행). 첫 모션 게이트가 `arm7_enable` 에서 뜬다.
3. `arm7_pick_and_place_head`(≥ 31 행) → `arm7_zensor`(≥ 16 행): head 장착, zensor 동작.
4. `cell5_z_dispense`(Z 421 mm 하강) → `cell5_dispense`(`pump/cycle` 1 × `c5_volume_uL` = 50 µL) → `cell5_z_home`(Z 상단 복귀): 분석 펌프가 홀더 안 vial 에 분주.
5. `arm7_zensor_wash`(≥ 61 행, ≥ 55 s, 약 110 s) → `cell5_fan_on`: 세척 후 선풍기 on → `arm7_init_head`(≥ 28 행): head 초기화.
6. `parallel[rail_home_shutdown, init_cell1/2/3_shutdown]`: 레일 홈 + 갠트리 3개 재홈을 동시에(블록은 가장 긴 자식 — 레일 ~40 s — 가 끝나야 끝남; 각 자식 timeout 180 s) → `verify_shutdown`.
7. `cell5_fan_off`(레일 홈 도착 시점) → `cell5_heater_off`(`on_fail: retry`, 2회 — 10-14 핫플레이트 USB 순단 대응) → `verify_fan_heater_off`.
8. `final_rail`/`final_cell1` → `verify_end_state`: 레일 17.98 ± 2, cell1 Z ≤ 3.5.


---

## 통합 Process(Synthesis+Analysis)

### `1cycle_1014.yaml`

**메인 체인.** `init_1014` → `synthesis_1014` → `analysis_1014` 를 한 파일로 이어 돌린 것(세 파일은 이 파일에서 잘라낸 것이라 스텝 블록·주석·`params` 가 같다). 10-14 에 `test_synthesis_to_analysis_shinyeong.yaml` 에서 이름만 바꿈. 촬영·반복 검증은 이 파일로, 단계별 재시도는 위 세 파일로.

- **Directory**: `scenarios/shinyeong_test/1cycle_1014.yaml` (104 스텝)

- **Run Command**:
```Terminal
cd ~/workspace/InnoCOREServer/InnoCORESDL_Sungwoo
.venv/bin/python -m orchestrator validate scenarios/shinyeong_test/1cycle_1014.yaml
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/1cycle_1014.yaml
# 짧은 확인용(분주 1 cycle): --param dispense_cycles=1
```

- **Input**:
	- args: 좌표·cycle 수를 바꾼 뒤에는 `--step-mode`. `--param dispense_cycles=1` 로 2분짜리 분주 3회를 7초로 줄일 수 있음
	- param: §6 전부. 핵심은 `dispense_cycles`(20), `volume_uL`(100), `hotplate_target_c`(100), `c5_volume_uL`(50), `c5_z_dispense_mm`(421), `rail_*_mm`, `c1/2/3_x/z_mm`
	- 전제: **7개 cell 서버 전부**. 저울 위 vial 없음, 58번 랙에 새 vial(≥ 6 mL), 공급액 각 2 mL 이상. 레일은 어디 있어도 됨(첫 모션이 갠트리 홈, 그 다음 `rail_home`). cell7 펜던트 잔여 없음, cell6/7 컨트롤러에 lua 6개.

- **Output**: 벤치가 시작 상태로 복귀 — 레일 17.98, 갠트리 3개 0, cell5 Z 상단, 히터·선풍기 off. `vars.json`: `d1..d7`, `prog`(cell6), `prog7v/h/z/w/i`(cell7), `disp/disp2/disp3`(`cycles_done` == 20), `disp5`, `c5z`/`c5home`, `hp_t`/`hp_on`/`hp_off`, `fan_on`/`fan_off`, `railback`, `h1end/h2end/h3end`. 약 15 분. run 기록(옛 이름 포함 30 runs): 10-13 `20261013T152247Z` completed(103 스텝), `20261013T154256Z` failed(마지막 `cell5_heater_off` 만 — 핫플레이트 USB 순단), **`20261013T164947Z` completed — 104 스텝 기준 런, 촬영본**(선풍기 56 s, 히터 off 1회 성공). 이 폴더명 변경 뒤의 run 은 `runs/<UTC>-one_cycle_1014` 로 남는다.

- **Process 순서**: `init_1014` 1~5 → `synthesis_1014` 2~4 → `analysis_1014` 2~8 과 같다(통합 파일에는 단계 사이 프리플라이트 `pre_rail_home`/`pre_rail` 이 없음 — 앞 단계가 그 상태를 만들었으므로).


---

## 보조·검증 Scenario

### `test_vial_place_to_end_shinyeong.yaml`

linear Rail 지정 위치 테스트용 + 분석 단계의 **전제 상태(vial 이 저울 위, 레일이 end) 를 빨리 만드는** 준비용. 분주 없음.

- **Directory**: `scenarios/shinyeong_test/test_vial_place_to_end_shinyeong.yaml` (17 스텝)

- **Run Command**:
```Terminal
cd $REPO
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/test_vial_place_to_end_shinyeong.yaml
```
- **Input**:
	- args: 없음
	- param: `program`, `arm_last_line`/`arm_min_elapsed_s`, `rail_home_mm`, `rail_end_mm`, `rail_tol_mm`
	- 전제: cell4, 6 서버 + cell1/2/3 서버(헤드 올라가 있는지 확인용). 저울 비어 있음.

- **Output**: 레일이 978 mm 에 **머무른 채** 끝남. 1 run `20260930T144745Z` completed 1분 56초.

- **Process 순서**
1. `diag_cell6/4/1/2/3`: arm ready, 저울 ok, 헤드 3개 up.
2. `rail_home` → `rail_park_home`: 레일 17.98 mm.
3. `arm_enable` → `arm_program`: cell6 이 vial 을 저울 위에.
4. `rail_to_end`: 레일 978 mm → `verify_rail_at_end`.

### `test_dispense_weigh_shinyeong.yaml`

**분주량 검증(밀도 체크).** cell1→2→3 순으로 저울 위 vial 에 분주하고 무게를 읽는다. 산출물은 무게값.

- **Directory**: `scenarios/shinyeong_test/test_dispense_weigh_shinyeong.yaml` (54 스텝)

- **Run Command**:
```Terminal
cd $REPO
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/test_dispense_weigh_shinyeong.yaml --param weigh_cycles=3
```
- **Input**:
	- args: `--param weigh_cycles=N` 으로 분주 횟수 변경
	- param: 통합 파일의 `rail_*_mm`, `c1/2/3_x/z_mm`, `volume_uL`(100), 포트/force + `weigh_cycles`(3 → 300 µL/cell). **이 파일의 `c1/c2_z_mm` 는 아직 225** — 10-13 이후 cell1 Z_A 하한이 224.3 이라 `--param c1_z_mm=223 --param c2_z_mm=223` 을 줄 것
	- 전제: cell1, 2, 3, 4 서버. 저울 위 **빈 vial**. 헤드 3개 up(첫 스텝이 홈이 아니라 cell1 로 레일 이동).

- **Output**: `vars.json` 의 `z1/z2/z3`(tare 후 영점 readback, ≤ 0.05 g 이어야 함), `w1/w2/w3`(분주 후, > 0.05 g). 1 run `20260930T141111Z` failed — cell1 0.3066 g 측정 뒤 `cell2_descend` 에서 Z 가 225 mm 명령에 210.81 mm 에서 멈춤. cell2·cell3 는 같은 날 cell1 블록을 뺀 사본(삭제됨)으로 `20260930T141552Z` completed — **cell2 0.3363 g, cell3 0.3185 g**(300 µL 기준). 세 값이 모두 모임.
```Terminal
R=runs/$(ls runs | grep test_dispense_weigh | sort | tail -1)
python3 -c "import json;v=json.load(open('$R/vars.json'));print({k:v[k].get('weight_g') for k in v if k[0] in 'zw'})"
```

- **Process 순서** (cell N = 1, 2, 3 반복)
1. `diag_cell4/1/2/3`.
2. `rail_at_cellN`: 레일을 스테이션으로.
3. `tare_cellN` → `zero_cellN`: tare 뒤 `balance/weight` 를 다시 읽어 영점 확인(tare 응답은 측정값이 아님).
4. `cellN_x_over_vial` → `cellN_descend`: 헤드를 vial 위로, 하강.
5. `cellN_pump_init` → `cellN_dispense`: `pump/cycle` weigh_cycles × 100 µL.
6. `cellN_retract` → `weigh_cellN`: Z 복귀 후 무게 읽기.
7. 세 cell 뒤 `rail_home`: 레일 홈.

### 정리된 파일 (2026-10-14 삭제)

`test_synthesis_seq_shinyeong` / `test_synthesis_seq_free_shinyeong`(합성 원본·free run, `c1/c2_z_mm` 225 또는 1 × 100 µL) 과 `test_analysis_tail_shinyeong` / `test_analysis_tail_from_zensor_shinyeong`(분석 tail, 팬·히터 off 없음) 은 1014 계열이 대신하므로 삭제함. 내용은 git 이력(`570edd8` 이전) 과 run 기록 `runs/<UTC>-<그 이름>` 에 남아 있다. `1cycle_1014.yaml` 헤더 주석의 "A COPY of test_synthesis_seq_free_shinyeong" 은 계보 기록이라 그대로 둠.

---

## 공통 `params`

통합 Process `1cycle_1014.yaml` 기준(2026-10-14 15:30). `init_1014`/`synthesis_1014`/`analysis_1014` 는 같은 값(파일마다 쓰는 키만 다름). `test_dispense_weigh`/`test_vial_place_to_end` 는 09-30 사본(차이는 각 파일 Input 에 적음).

| Params | Value (unit) | Meaning |
| ------ | ------------ | ------- |
| `program` | `Task1_original.lua` | cell6 vial 배치 프로그램(컨트롤러 `/fruser/` 에 있음) |
| `arm_last_line` / `arm_min_elapsed_s` | 29 / 20.0 (s) | cell6 프로그램 완료 판정 하한(마지막 줄, 최소 소요) |
| `analysis_program_vial` | `pick_and_place_vial.lua` | cell7: 저울 위 vial → 분석 홀더 |
| `analysis_program_head` | `pick_and_place_head.lua` | cell7: head 장착 |
| `analysis_program_zensor` | `zensor.lua` | cell7: zensor 동작 |
| `analysis_program_wash` | `ZensorWash.lua` | cell7: 세척 |
| `analysis_program_init_head` | `init_head.lua` | cell7: head 초기화 |
| `analysis_{vial,head,zensor,wash,init_head}_last_line` | 25 / 31 / 16 / 61 / 28 | 각 프로그램 완료 판정 마지막 줄 |
| `analysis_{vial,head,zensor,wash,init_head}_min_s` | 10 / 10 / 10 / 55 / 10 (s) | 각 프로그램 최소 소요 |
| `rail_home_mm` | 17.98 (mm) | cell4 레일 홈. 서버의 home_mm(5.0) 과 달라 명시적 `linear/move` |
| `rail_cell1_mm` / `rail_cell2_mm` / `rail_cell3_mm` | 491.752 / 729.246 / 947.5 (mm) | 각 스테이션 레일 위치(09-30 재영점 후) |
| `rail_end_mm` | 978.0 (mm) | cell7 이 vial 을 집는 위치. 기계적 끝 980.37, 987.98 은 스톨+amp fault |
| `c1_x_mm` / `c2_x_mm` / `c3_x_mm` | 249.985 / 250.004 / 243.0 (mm) | vial 위 갠트리 X |
| `c1_z_mm` / `c2_z_mm` / `c3_z_mm` | 223.0 / 223.0 / 223.0 (mm) | 하강 Z. **Z+ 가 아래**, 0 = 올라간 상태. 10-13 에 225→223(cell1 Z_A 가 224.3 에서 하한에 닿음). `test_dispense_weigh` 는 아직 225 |
| `z_up_mm` | 3.0 (mm) | "헤드가 올라가 있다" 판정 상한 |
| `volume_uL` | 100.0 (µL) | 합성 분주 1 cycle 양 |
| `dispense_cycles` | 20 | 합성 분주 cycle 수 → 2 mL/cell(125 µL 시린지, 약 7 s/cycle). 10-13 추가 |
| `source_port` / `dispense_port` | 1 / 2 | 펌프 밸브 명령: 1 = 공급(물리 포트 1), 2 = 노즐(물리 포트 3) |
| `init_force` | 2 | 펌프 홈 힘 코드(125 µL 시린지 = 1/3 힘) |
| `c5_z_dispense_mm` | 421.0 (mm) | cell5 분주 Z. 0 = 상단 스위치, +mm 아래, 하단 end-stop ≈ 422.6 |
| `c5_z_tol_mm` | 0.5 (mm) | cell5 Z verify 허용 오차 |
| `c5_volume_uL` | 50.0 (µL) | cell5 분주량. 10-12 75 → 10-13 25 → 10-14 50. 1 cycle 상한 125 µL(시린지). 며칠 쉬면 호스가 비니 런 전 펌프 3~5 cycle 프라이밍 |
| `hotplate_target_c` | 100.0 (°C) | cell5 핫플레이트 설정 온도(10-14). cell5.toml 상한은 NUC2 에서 110 으로 올림 |
| `rail_tol_mm` / `gantry_tol_mm` | 2.0 / 0.5 (mm) | 레일 / 갠트리 verify 허용 오차 |
| `speed_pct` / `accel_pct` | 10 / 0 (%) | arm·cell5 Z 속도·가속 |
| `weigh_cycles` | 3 | `test_dispense_weigh*` 만. 분주 반복 수 |
