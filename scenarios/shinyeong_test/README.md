# InnoCORE $H_2$ Project Scenario Description


## Overview

Scenario `.yaml` 파일들에 대한 설명.
전체적인 구조, Device 구동 low-level code 등에 대한 자세한 설명은 해당 repo 참조 바람
https://github.com/coport-uni/InnoCORESDL_Sungwoo

> 기준 시점: 2026-10-14 02:30 (벤치 체크아웃 `InnoCORESDL_Sungwoo` `main`, HEAD `57de7af` + 미커밋 작업 트리). 10-14 02:30 검증: 18개 파일의 스텝 수·params·run 기록을 실제 파일과 대조해 갱신함.

Contents

1. Synthesis — 합성 단계만 (2개)
2. Analysis — 분석 단계만 (2개)
3. 통합 Process(Synthesis+Analysis) — 합성 → 분석 전체 (1개)
4. 보조·검증 — 준비·측정 (2개)
5. 공통 `params`

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
	- param: 각 YAML 의 `params` 블록. 값은 §5 표. 파일마다 쓰는 키만 아래에 적는다.
	- 전제(모든 파일): 그 파일이 쓰는 cell 서버가 모두 떠 있고 `orchestrator/config.toml` 에 주석 해제돼 있을 것. 첫 모션 스텝에서 콘솔에 `y` 를 입력해야 진행된다(우회 불가).

- **Output**: `runs/<UTC>-<name>/` (gitignore)
	- `meta.json` — `state`(completed / failed / aborted), 시작·종료 시각, `git_commit`, 그때의 주소록
	- `run.jsonl` — 스텝 한 줄씩: id, cell, action, 응답, `ok`, `error`, 소요 시간. **측정값은 여기**
	- `vars.json` — `save_as` 로 저장한 응답 전부
	- `scenario.yaml` — 실행 시점 사본(`--param` 반영)
	- 파일별 "Output" 에는 그 run 에서 추가로 남는 값만 적는다.

- **Process 순서**: 아래 각 파일에 스텝 id 와 함께 적는다. 모든 move 뒤에는 엔코더 readback 을 assert 로 비교하는 `verify_*` 스텝이 따라오며(200 OK 가 아니라 이 assert 가 "움직였다"의 증거), 그 verify 스텝은 생략한다.

- **안전(모든 파일 공통)**: 레일(cell4)은 소프트웨어로 멈추지 않고(GAP-1), 다른 cell 의 `POST /v1/stop` 도 진행 중 move 뒤에 큐된다(GAP-9). **물리 e-stop 을 든 사람이 있어야 한다.** e-stop 뒤에는 cell7 gripper 를 다시 활성화할 것(안 하면 lua 가 11/14행에서 끝남). 상세는 루트 README `### Safety gaps`.



---

## Synthesis

### `test_synthesis_seq_shinyeong.yaml`

합성 단계 원본 — cell6 arm 이 vial 을 놓고, 레일이 cell1→2→3 아래로 운반, 각 cell 이 분주. **`pause` 9개**가 각 하강·분주 앞에 있어 작업자가 헤드와 vial 사이 틈을 눈으로 확인한다. **주의(10-14)**: 이 파일은 아직 `c1/c2_z_mm` 225 와 1 × 100 µL 분주를 갖고 있다 — `_free` 와 통합 파일에만 10-13/14 의 223 mm·20 cycle 이 반영돼 있다. 게이트판을 쓸 땐 `--param c1_z_mm=223 --param c2_z_mm=223` 을 주거나 파일을 먼저 맞출 것. **헤더에 모든 좌표의 측정 근거와 순서의 이유가 적혀 있다** — 다른 합성 파일 헤더는 전부 이 파일을 가리킨다.

- **Directory**: `scenarios/shinyeong_test/test_synthesis_seq_shinyeong.yaml` (73 스텝)

- **Run Command**:
```Terminal
cd $REPO
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/test_synthesis_seq_shinyeong.yaml --step-mode
```
- **Input**:
	- args: 게이트가 목적인 파일이므로 `--step-mode` 권장
	- param: `program`(Task1_original.lua), `arm_last_line`/`arm_min_elapsed_s`, `rail_home_mm`, `rail_cell1/2/3_mm`, `rail_end_mm`, `c1/2/3_x_mm`, `c1/2/3_z_mm`, `volume_uL`(100), `source_port`/`dispense_port`/`init_force`, `z_up_mm`, `rail_tol_mm`/`gantry_tol_mm`
	- 전제: cell1, 2, 3, 4, 6 서버. 저울 위에 이전 vial 없음.

- **Output**: 레일이 end 까지 간 뒤 **홈으로 돌아와** 끝남. `vars.json` 에 `prog`(cell6 lua 의 `completed`/`last_line`/`elapsed_s`), `disp`/`disp2`/`disp3`(`cycles_done`). 이 이름의 run 기록은 없음(실제 실행은 아래 `_free` 로).

- **Process 순서**
1. `diag_cell6/4/1/2/3` + `verify_*`: 5개 cell 의 `diagnose` 를 읽어 arm ready, 저울 ok, 헤드 3개가 올라가 있는지(Z ≤ 3.5 mm) 확인. 실패하면 아무것도 움직이기 전에 중단.
2. `init_home_cell1/2/3`: 갠트리 3개를 차례로 홈(스위치에서 0). 첫 모션 게이트가 여기서 뜬다.
3. `rail_home`: cell4 레일을 `rail_home_mm`(17.98 mm) 로. arm 프로그램이 저울이 홈에 있다고 가정하므로 arm 보다 먼저.
4. `arm_enable` → `arm_program`: cell6 이 `Task1_original.lua` 를 실행해 vial 을 저울 위에 놓는다(last_line ≥ 29, ≥ 20 s 로 완료 판정).
5. cell N = 1, 2, 3 각각 (`pause` 로 하강·분주 전 확인):
   `rail_at_cellN`(레일을 스테이션으로) → `cellN_pump_init`(펌프 홈, plunger 0) → `cellN_x_over_vial`(X 를 vial 위로) → `cellN_descend`(Z 하강 — 이 파일은 225 mm, `_free`/통합은 223 mm) → `cellN_dispense`(`pump/cycle` 1 × 100 µL — `_free`/통합은 `dispense_cycles` 20 × 100 µL = 2 mL, 공급 포트 1 → 노즐 포트 2) → `cellN_retract`(Z 0) → `cellN_status` 로 Z 가 올라왔는지 확인 — **이 확인이 다음 레일 이동의 전제**(헤드-레일 충돌 방지).
6. `rail_to_end`: 레일을 `rail_end_mm`(978 mm) 로.
7. `rail_return_home`: 레일 홈 복귀 → `verify_rail_home`.
8. `park_cell1/2/3`: 갠트리 3개를 파킹 위치로 → `verify_parked` → `final_rail`/`final_cell1`(status) → `verify_end_state`.

### `test_synthesis_seq_free_shinyeong.yaml`

위 파일에서 `pause` 9개를 뺀 **촬영용 free run**. 10-14 부터는 통합 파일의 합성 값(`c1/c2_z_mm` 223, `dispense_cycles` 20 → 2 mL/cell, 분주 timeout 360 s)도 함께 갖는다 — `_seq` 와의 차이는 pause 와 이 두 값. `_seq` 를 step-mode 로 돌려 cell2/3 의 하강이 저울 팬을 비켜가는 것을 본 뒤에만 쓴다(cell2/3 하강 높이는 cell1 값을 물려받은 것).

- **Directory**: `scenarios/shinyeong_test/test_synthesis_seq_free_shinyeong.yaml` (73 스텝)

- **Run Command**:
```Terminal
cd $REPO
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/test_synthesis_seq_free_shinyeong.yaml
```
- **Input**:
	- args: 없음(첫 모션 게이트만)
	- param: `_seq` + `dispense_cycles`(20). `--param dispense_cycles=5` 로 500 µL/cell(호스 채우기용)
	- 전제: `_seq` 와 동일. 2 mL × 3 이 들어갈 vial(≥ 6 mL)과 각 펌프 공급액 2 mL 이상

- **Output**: `_seq` 와 동일. 59 runs(08-13~10-14). 10-14: `20261013T162332Z` completed(2 mL × 3), `20261013T163732Z`/`20261013T164330Z` completed(500 µL × 3, `dispense_cycles=5`). 이 폴더에서 가장 많이 돈 파일.

- **Process 순서**: `_seq` 의 1~8 과 같고 `pause` 가 없다.

---

## Analysis

두 파일 모두 통합 Process 의 **cell7 tail(`arm7_enable` .. `verify_end_state`)** 을 떼어낸 것이다. 합성이 끝났는데 분석 쪽에서 실패했을 때 합성을 다시 돌리지 않고 tail 만 재시도한다. 시작 지점만 다르다: vial 집기부터 / zensor 부터. (10-14 정리: `_from_head`, `_nocell1`, `_dispense_weigh_c23`, `_noarm`, `test_arm_program_cell6_task1`, `test_gantry_*` 6개는 미사용·구버전이라 삭제. 8월 bring-up 파일과 노암판은 git 이력(`57de7af` 이전)에 남아 있고, 나머지는 통합 파일에서 스텝 id 로 다시 만든다.)

### `test_analysis_tail_shinyeong.yaml`

분석 tail 전체. **레일이 이미 end 에 있고 헤드 3개가 올라가 있지 않으면 시작을 거부**한다.

- **Directory**: `scenarios/shinyeong_test/test_analysis_tail_shinyeong.yaml` (38 스텝)

- **Run Command**:
```Terminal
cd $REPO
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/test_analysis_tail_shinyeong.yaml --step-mode
```
- **Input**:
	- args: 좌표를 바꾼 뒤에는 `--step-mode` 먼저
	- param: `analysis_program_{vial,head,zensor,wash,init_head}`, 각 `_last_line`/`_min_s`, `rail_end_mm`/`rail_home_mm`/`rail_tol_mm`, `c5_z_dispense_mm`(421), `c5_volume_uL`(50 — 10-14 에 통합·tail·from_zensor 모두 50 으로 통일), `c5_z_tol_mm`
	- 전제: cell7, 5, 4 서버 + cell1/2/3 서버(확인·재홈용). vial 이 저울 위, 레일이 `rail_end_mm`.

- **Output**: `prog7v/h/z/w/i`, `disp5`, `c5z`/`c5home`. 끝에 레일 홈 + 갠트리 재홈. 4 runs: `20260930T160502Z` failed(cell5 없는 26 스텝 버전), 10-12 `20261011T232810Z` completed(cell5 25 µL)·`20261011T234215Z` completed(75 µL) — cell5 포함 38 스텝의 첫 완주.

- **Process 순서**
1. `diag_cell7/4/1/2/3` → `pre_rail`(cell4 status) → `verify_rail_at_end_pre`: 레일이 978 ± 2 mm 이고 cell1/2/3 Z_A 가 모두 ≤ 3.5 mm(`z_up_mm + gantry_tol_mm`, 즉 올라가 있음)인지. 아니면 거부.
2. `diag_cell5` → `init_home_cell5`(Z 를 상단 스위치로) → `cell5_pump_init`(분석 펌프 홈).
3. `arm7_enable` → `arm7_pick_and_place_vial`: cell7 이 저울 위 vial 을 분석 홀더로.
4. `arm7_pick_and_place_head`: head 장착.
5. `arm7_zensor`: zensor.lua.
6. `cell5_z_dispense`(Z 421 mm 하강) → `cell5_dispense`(`pump/cycle` 1 × `c5_volume_uL`) → `cell5_z_home`(Z 상단 복귀).
7. `arm7_zensor_wash`: ZensorWash.lua(≥ 55 s).
8. `arm7_init_head`: init_head.lua.
9. `parallel[rail_home_shutdown, init_cell1/2/3_shutdown]`: 레일 홈과 갠트리 3개 재홈을 동시에 → `verify_shutdown` → `final_rail`/`final_cell1` → `verify_end_state`.

### `test_analysis_tail_from_zensor_shinyeong.yaml`

`zensor.lua` 부터 시작 — vial **과 head 가 이미 장착**된 상태. **레일 위치는 확인하지 않는다.** cell5 분주 구간을 가장 짧게 반복 테스트할 수 있는 파일.

- **Directory**: `scenarios/shinyeong_test/test_analysis_tail_from_zensor_shinyeong.yaml` (32 스텝)

- **Run Command**:
```Terminal
cd $REPO
.venv/bin/python -m orchestrator run scenarios/shinyeong_test/test_analysis_tail_from_zensor_shinyeong.yaml --step-mode
```
- **Input**:
	- args: `--step-mode`
	- param: `test_analysis_tail` 과 동일(cell5 포함)
	- 전제: cell7, 5, 4 + cell1/2/3 서버. vial·head 장착 완료.

- **Output**: `prog7z/w/i`, `disp5`, `c5z`/`c5home`. 미실행.

- **Process 순서**: 위 1(단, `pre_rail` 확인 없음) → 2 → 5 → 6 → 7 → 8 → 9.



---

## 통합 Process(Synthesis+Analysis)

### `test_synthesis_to_analysis_shinyeong.yaml`

**메인 체인.** Synthesis(`_seq_free`) 뒤에 레일을 end 에 세운 채 cell7 이 vial 을 받아 분석을 끝내고, 마지막에 벤치를 시작 상태로 되돌린다. 2026-10-12 에 cell5 분주, 10-13 에 핫플레이트·선풍기·20 cycle 분주가 들어갔다. 다른 파일은 모두 "이 파일과 params 를 맞춰라" 고 가리킨다.

- **Directory**: `scenarios/shinyeong_test/test_synthesis_to_analysis_shinyeong.yaml` (104 스텝, 10-14 02:30 기준)

- **Run Command**:
```Terminal
cd $REPO
.venv/bin/python -m orchestrator validate scenarios/shinyeong_test/test_synthesis_to_analysis_shinyeong.yaml
.venv/bin/python -m orchestrator run      scenarios/shinyeong_test/test_synthesis_to_analysis_shinyeong.yaml --step-mode
.venv/bin/python -m orchestrator run      scenarios/shinyeong_test/test_synthesis_to_analysis_shinyeong.yaml
.venv/bin/python -m orchestrator run      scenarios/shinyeong_test/test_synthesis_to_analysis_shinyeong.yaml --param dispense_cycles=1   # 짧은 확인용
```
- **Input**:
	- args: 좌표나 cycle 수를 바꾼 뒤에는 `--step-mode`. `--param dispense_cycles=1` 로 2분짜리 분주 3회를 7초로 줄일 수 있음
	- param: §5 전부. 핵심은 `dispense_cycles`(20), `volume_uL`(100), `hotplate_target_c`(100), `c5_volume_uL`(50), `c5_z_dispense_mm`(421), `rail_*_mm`, `c1/2/3_x/z_mm`
	- 전제: **7개 cell 서버 전부**. 저울 위 vial 없음. 레일은 어디 있어도 됨(첫 모션이 `rail_home`). cell7 펜던트 잔여 상태 없음(`arm/program` 이 409 "already has a job program" 이면 `curl -X POST localhost:17066/v1/stop`). cell6/7 컨트롤러에 lua 6개. NUC2 에서 cell5 는 **`InnoCORESDL_shinyeong` 체크아웃**에서 `claude_test/test_cell5_server_shinyeong.py --config server/nuc2/cell5.toml --max-travel 450` 로 기동(IKA 가 있으면 실제 핫플레이트, 없으면 거부 stub; 팬·핫플레이트 코드는 10-14 에 `_Sungwoo` 작업 트리에서 그 체크아웃으로 복사됨, 원본은 `_shinyeong_backup_20261014/`). 포트 17062 는 한 프로세스만 — 다른 작업자의 정식 `python -m server` 가 떠 있으면 먼저 내려야 한다(정식 서버는 Z 400 mm 한계라 421 을 못 간다).

- **Output**: 벤치가 시작 상태로 복귀 — 레일 17.98, 갠트리 3개 0, cell5 Z 상단, 히터·선풍기 off. `vars.json`: `d1..d7`, `prog`(cell6), `prog7v/h/z/w/i`(cell7), `disp/disp2/disp3`(`cycles_done` == 20), `disp5`, `c5z`/`c5home`, `hp_t`/`hp_on`/`hp_off`, `fan_on`/`fan_off`.
	run 기록: 30 runs. 09-15~09-30 22 runs(cell5 없는 85 스텝) + 10-12 2 runs completed(cell5 75 µL, 7분 53초) + 10-13(UTC) 6 runs — 3 failed(레일 스톨 3.796 mm / cell1 Z 224.33 미도달 / cell2 retract 500: Z_B 어댑터 USB 순단), `20261013T152247Z` completed 15분 10초(20 cycle 분주 3회가 각 139 s), `20261013T154256Z` failed(마지막 `cell5_heater_off` 만 — 핫플레이트 USB 순단, 히터는 수동 재시도로 off), **`20261013T164947Z` completed — 104 스텝 기준 런**(선풍기 17:03:23→17:04:19 UTC 56 s, 히터 off 1회 성공).

- **Process 순서**
1. `diag_cell6/7/4/1/2/3/5` + `verify_*`: 7개 cell 의 `diagnose`. arm ready ×2, 저울 ok, cell1 펌프 있음, cell5 pump·stage ok, 헤드 3개 올라가 있음.
2. `init_home_cell1/2/3`: 갠트리 홈(첫 모션 게이트). `init_home_cell5`: cell5 Z 를 상단 스위치로. `cell5_pump_init`: 분석 펌프 홈.
3. `cell5_hotplate_target`(100 °C) → `cell5_heater_on`: 핫플레이트 예열 시작. 온도 도달을 기다리지는 않는다.
4. `rail_home` → `rail_park_home`: 레일을 17.98 mm 로.
5. `arm_enable` → `arm_program`: cell6 이 `Task1_original.lua` 로 vial 을 저울 위에 놓는다.
6. cell N = 1, 2, 3 각각: `rail_at_cellN` → `cellN_pump_init` → `cellN_x_over_vial` → `cellN_descend`(Z 223 mm) → `cellN_dispense`(**`pump/cycle` 20 × 100 µL = 2 mL**, 약 139 s) → `cellN_retract` → `cellN_status`(Z 0 확인, 다음 레일 이동의 전제).
7. `rail_to_end`(978 mm) → `cell3_x_home`: 갠트리 파킹.
8. `arm7_enable` → `arm7_pick_and_place_vial` → `arm7_pick_and_place_head` → `arm7_zensor`: cell7 이 vial 을 홀더로, head 장착, zensor.
9. `cell5_z_dispense`(Z 421 mm) → `cell5_dispense`(1 × 50 µL; 1 cycle 최대 125 µL = 시린지) → `cell5_z_home`: 분석 펌프가 홀더 안 vial 에 분주하고 Z 복귀.
10. `arm7_zensor_wash`(약 110 s) → `cell5_fan_on` → `arm7_init_head`: 세척, 선풍기 켬, head 초기화.
11. `parallel[rail_home_shutdown, init_cell1/2/3_shutdown]`: 레일 홈 + 갠트리 3개 재홈 동시에 → `verify_shutdown`.
12. `cell5_fan_off` → `cell5_heater_off`(`on_fail: retry`, 2회) → `verify_fan_heater_off`: 선풍기·히터 off(10-14: 선풍기 off 를 parallel 블록 **밖**으로 — 블록 안에서는 시작 시점에 꺼져 15 s 만 돌았음. 히터 off 는 10-14 핫플레이트 USB(허브 3-4) 순단으로 한 번 실패해 재시도를 붙임).
13. `final_rail`/`final_cell1` → `verify_end_state`: 레일 17.98 ± 2, cell1 Z ≤ 3.5.

---

## 보조·검증 Scenario

### `test_vial_place_to_end_shinyeong.yaml`

linear Rail 지정 위치 테스트용 + 분석 tail 의 **전제 상태(vial 이 저울 위, 레일이 end) 를 빨리 만드는** 준비용. 분주 없음.

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
	- param: 통합 파일의 `rail_*_mm`, `c1/2/3_x/z_mm`, `volume_uL`(100), 포트/force + `weigh_cycles`(3 → 300 µL/cell)
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

---

## 공통 `params`

통합 Process `test_synthesis_to_analysis_shinyeong.yaml` 기준(2026-10-14 02:30). 다른 파일은 이 값의 09-30 또는 10-12 사본이다(차이는 각 파일 Input 에 적음).

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
| `c1_z_mm` / `c2_z_mm` / `c3_z_mm` | 223.0 / 223.0 / 223.0 (mm) | 하강 Z. **Z+ 가 아래**, 0 = 올라간 상태. 10-13 에 225→223(cell1 Z_A 가 224.3 에서 하한에 닿음). `_seq`·tail·dispense_weigh·vial_place 사본은 아직 225 |
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

