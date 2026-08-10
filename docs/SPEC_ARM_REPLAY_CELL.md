# 개발사양서: ArmReplayCell (cell6, cell7)

대상 저장소: `coport-uni/InnoCORESDL_Sungwoo`
연계 저장소: `coport-uni/FR5ControllerVLA` (huggingface/lerobot fork)
문서 버전: v1.0, 2026-08-10
실행 주체: Claude Code

---

## 1. 목적

FR5 로봇 팔 2대를 InnoCORESDL의 L1 cell로 편입한다. 각 팔은 독립된 cell
프로세스(cell6, cell7)로 서비스되며, lerobot의 replay 기능으로 사전 기록된
dataset episode를 재생하는 것을 유일한 모션 수단으로 한다. replay 대상은
HuggingFace repo_id와 episode 번호를 요청 시점에 변수로 받는다.

본 사양서는 TDD(Test Driven Development)로 진행한다. 모든 구현 코드는
대응하는 테스트가 먼저 작성되고 실패하는 것을 확인한 뒤에 작성한다.
최종 인수 테스트는 실제 로봇 2대의 joint 1을 각각 +10도 기동하고 encoder
값으로 실제 이동을 검증하는 하드웨어 테스트(T1)를 포함한다.

## 2. 범위

포함:

- `cell/arm_replay_cell.py`: `Cell` protocol 구현체 1개 (두 cell이 공유)
- `server/` 라우트, 스키마, 에러 매핑, `--cell arm_replay` 로더
- `server/<nuc>/cell6.toml.example`, `server/<nuc>/cell7.toml.example`
- `claude_test/` 단위 테스트 (하드웨어 불요) 및 bench 스크립트
  `claude_test/smoke_arm.py` (하드웨어 필요, 게이트 필수)
- `scenarios/demo_arm_replay.yaml`
- `external/SUBMODULES.md` 갱신, `docs/L1_AUDIT.md` 기록 항목 추가

제외 (비범위):

- 연속 pose/teleop 인터페이스. arm action set은 replay 전용이다.
- VLA 정책 추론 실행 (record, train, eval). 본 사양은 replay만 다룬다.
- 두 팔의 작업 공간 충돌 방지 로직 (GAP-8). 시나리오 작성 규칙으로만
  문서화한다.
- L2 orchestrator 코드 변경. 변경량이 0이어야 정상이다 (cell5 선례).

## 3. 아키텍처 결정 (확정 사항, 변경 금지)

| ID | 결정 | 근거 |
|---|---|---|
| D1 | 팔 1대 = cell 1개. cell6 = 1호기, cell7 = 2호기. 동일 shape `ArmReplayCell`을 config만 바꿔 복제 | cell1~3 복제 관례. L2 lock이 cell 단위이므로 cell 분리가 곧 팔 단위 lock |
| D2 | 포트: cell6 = 17064, cell7 = 17066 | 기존 포트 테이블 규칙 (cell당 +2) |
| D3 | replay는 lerobot conda env에서 `lerobot-replay` CLI를 subprocess로 실행. SDL venv에 lerobot을 설치하지 않는다 | torch 의존성 격리. subprocess kill로 stop()이 실제 동작 (GAP-9 미상속) |
| D4 | `FR5ControllerVLA`를 `external/FR5ControllerVLA` submodule로 pin. 단 editable install 하지 않음. 예외 사유를 `SUBMODULES.md`에 명기 | 버전 고정은 submodule로, 실행은 자체 env로 |
| D5 | repo_id, episode, fps는 요청 body의 변수. fps는 null이면 기록값 사용, 지정 시 기록값과 불일치하면 400 거부 | fps 가속 재생은 ServoJ 간격 압축으로 위험 |
| D6 | repo_id는 config의 `allowed_repo_prefixes` 검사를 통과해야 함 | 임의 인터넷 dataset의 물리 재생 차단 |
| D7 | prefetch(다운로드)와 replay(모션)를 별도 route로 분리 | 모션 timeout에서 네트워크 시간 제거, 실패 원인 분리 |
| D8 | 10도 기동 테스트는 영구 `/v1` 라우트가 아니라 bench 스크립트 `claude_test/smoke_arm.py`로 구현 | arm action set을 replay 전용으로 유지 (ADDING_A_CELL.md의 discrete action 원칙) |

## 4. 산출물 파일 목록

| 경로 | 신규/수정 | 내용 |
|---|---|---|
| `cell/arm_replay_cell.py` | 신규 | `ArmReplayCell`, `ArmReplayConfig` dataclass |
| `server/schemas.py` (또는 관례에 맞는 위치) | 수정 | arm 요청/응답 pydantic 모델 |
| `server/routes.py` (관례 위치) | 수정 | `arm/*` 라우트 등록 |
| `server/__main__.py` | 수정 | `_load_arm_replay()`, `--cell arm_replay` 분기 |
| `server/<nuc>/cell6.toml.example` | 신규 | 1호기 config |
| `server/<nuc>/cell7.toml.example` | 신규 | 2호기 config |
| `claude_test/test_arm_replay_cell.py` | 신규 | T0 단위 테스트 |
| `claude_test/smoke_arm.py` | 신규 | T1 하드웨어 스모크 (joint 1 10도 테스트 포함) |
| `scenarios/demo_arm_replay.yaml` | 신규 | T4 시나리오 |
| `external/SUBMODULES.md` | 수정 | D4 예외 기록 |
| `requirements.txt` | 수정 없음 확인 | lerobot을 넣지 않는다 (D3) |

## 5. Config 스키마

```toml
# server/<nuc>/cell6.toml.example
[server]
port = 17064

[arm]
robot_id = "fr5_a"                # 로그, runlog, diagnose에 표기되는 팔 식별자
ip_address = "192.168.58.2"       # cell7은 반드시 다른 IP (사전 변경 필수)
gripper_enabled = true

conda_sh = "/home/inno-controller/anaconda3/etc/profile.d/conda.sh"
conda_env = "lerobot"
lerobot_root = "external/FR5ControllerVLA"

allowed_repo_prefixes = ["coport-uni/"]
cache_dir = ""                    # 빈 문자열이면 HF 기본 캐시
offline_only = false              # true면 미캐시 dataset은 replay 거부

max_replay_s = 300.0              # replay timeout 상한
replay_timeout_factor = 1.5      # timeout = frames / fps * factor
start_pose_tolerance_deg = 2.0    # 첫 frame과의 허용 편차, 초과 시 MoveJ 선행
final_pose_tolerance_deg = 1.0    # 종료 검증 기준 (응답에 오차 포함, 판정은 시나리오)
jog_speed_pct = 10.0              # smoke_arm.py의 MoveJ 속도 (안전 저속)
```

- 주소는 IP로 고정 자산이므로 VID:PID 규칙 비대상. 단 cell6과 cell7의
  `ip_address`가 동일하면 `open()`에서 기동 실패해야 한다는 요구는 없다.
  같은 프로세스가 아니므로 검증 불가. 대신 문서와 T1 게이트로 방어한다.
- config에 토큰류를 넣지 않는다. private dataset은 `HF_TOKEN` 환경 변수.

## 6. Cell 인터페이스 사양

`cell/cell_protocol.py`의 `Cell` protocol을 구현한다.

### 6.1 Substrate

| 메서드 | 동작 | 실패 시 |
|---|---|---|
| `open(config)` | 로봇 TCP 접속 확인 (SDK connect 후 joint 1회 읽기), conda_sh와 lerobot_root 존재 확인. 접속 불가면 서버 기동 실패 | `TransportError` |
| `diagnose()` | `{arm: {ok, robot_id, ip, reachable, gripper}, replay: {running, last}}` | 예외 없이 상태 보고 |
| `status()` | joint 각도 6개 (deg, encoder 실측), moving 여부, 마지막 replay 요약 | `TransportError` |
| `stop()` | (1) replay subprocess에 SIGTERM, 2초 내 미종료 시 SIGKILL, (2) SDK 정지 명령 호출, (3) 각 단계 결과를 dict로 반환. 어느 단계가 실패해도 다음 단계를 시도한다 (cell5 stop 패턴) | 부분 실패를 반환값에 기록 |
| `close()` | SDK 연결 해제, 실행 중 subprocess 정리 | best effort |

`stop()`은 replay 실행 lock을 기다리지 않아야 한다. 구현 규칙: replay는
worker에서 subprocess를 감시하고, stop은 프로세스 핸들에 직접 접근한다.
이 cell에서 GAP-9가 재현되면 결함이다.

### 6.2 arm action set

| Route | Method | Body | 성공 응답 (요지) |
|---|---|---|---|
| `arm/prefetch` | POST | `{repo_id, episode}` | `{cached: true, frames, fps, duration_s}` |
| `arm/replay` | POST | `{repo_id, episode, fps: int\|null}` | `{completed: true, frames, elapsed_s, final_joint_error_deg, joints_deg: [...]}` |

`arm/replay` 실행 시퀀스 (순서 고정):

1. repo_id prefix 검증. 실패 400.
2. dataset meta 조회. episode 범위 검증. 실패 400. 미캐시이고
   `offline_only=true`면 409.
3. timeout 산출: `min(frames / fps * replay_timeout_factor, max_replay_s)`.
4. 현재 joint 읽기. 첫 frame과의 최대 편차가 `start_pose_tolerance_deg`
   초과 시 저속 MoveJ로 첫 frame 자세로 선행 이동.
5. `lerobot-replay` subprocess 실행. 인자는 `6__replay.sh`와 동일 계열:
   `--robot.type=fairino_follower --robot.ip_address=<ip>
   --robot.gripper_enabled=<bool> --robot.id=<robot_id>
   --dataset.repo_id=<repo_id> --dataset.episode=<n> --dataset.fps=<fps>`.
6. 종료 코드와 timeout 판정.
7. **encoder 재독**: 종료 후 joint를 SDK로 실제로 다시 읽어
   `final_joint_error_deg` (마지막 frame 대비 최대 축 오차)를 응답에 포함.
   LearnedPatterns #24: 200 OK는 encoder를 읽었다는 뜻이어야 한다.

### 6.3 에러 매핑 (`CellError` 체계 준수)

| 상황 | 예외 | HTTP |
|---|---|---|
| prefix 불허, episode 범위 밖, fps 불일치 | `InvalidArgError` | 400 |
| replay 진행 중 재호출, offline_only 미캐시, 타 action set 호출 | `WrongStateError` | 409 |
| subprocess 비정상 종료 (0이 아닌 종료 코드), 종료 후 pose 오차 폭주 | `DeviceFaultError` | 500 |
| 로봇 TCP 단절, HF 네트워크 불능 (prefetch) | `TransportError` | 503 |
| replay timeout | `CellTimeoutError` | 504 |

미구현 action set (pump, balance, gantry 등) 호출은 기존 관례대로
`WrongStateError` 409.

## 7. TDD 구성

원칙: 각 레벨은 아래 레벨 통과를 전제한다. T0는 CI에서 상시 실행 가능,
T1 이상은 하드웨어와 operator가 필요하며 게이트 없이는 절대 실행하지
않는다. 구현 순서는 "테스트 작성 → red 확인 → 구현 → green"을 레벨별로
반복한다.

### T0. 단위 테스트 (하드웨어 불요, pytest, 최우선 작성)

파일: `claude_test/test_arm_replay_cell.py`
SDK와 subprocess는 mock. 최소 케이스:

| # | 케이스 | 기대 |
|---|---|---|
| T0-1 | config 파싱: cell6/cell7 example TOML 로드 | 모든 필드 타입 일치 |
| T0-2 | repo_id `"coport-uni/x"` 허용, `"evil/x"` 거부 | 거부는 `InvalidArgError` |
| T0-3 | episode가 total_episodes 이상 | `InvalidArgError` |
| T0-4 | fps=null이면 기록 fps 채택, fps=기록값이면 통과, 다르면 거부 | 거부는 `InvalidArgError` |
| T0-5 | timeout 산출식: frames=2400, fps=20, factor=1.5 → 180.0s, max_replay_s=100이면 100.0 | 정확 일치 |
| T0-6 | subprocess CLI 인자 조립: config와 body로부터 6__replay.sh와 동일 인자 생성 | 문자열 비교 |
| T0-7 | replay 중 재호출 | `WrongStateError` |
| T0-8 | subprocess 종료 코드 1 | `DeviceFaultError` |
| T0-9 | stop(): mock 프로세스 SIGTERM 후 SDK stop 호출 순서, SDK stop 실패 시에도 결과 dict에 기록되고 예외 미전파 | 호출 순서와 반환값 검증 |
| T0-10 | 시작 자세 편차 > tolerance → MoveJ 선행 호출, 이하 → 미호출 | mock 호출 여부 |

완료 기준: `pytest claude_test` 전체 green, `ruff check` 통과.

### T1. 하드웨어 스모크: joint 1 10도 기동 시험 (본 사양의 인수 테스트)

파일: `claude_test/smoke_arm.py`
실행 형태: `python claude_test/smoke_arm.py --ip 192.168.58.2 --robot-id fr5_a [--suite jog10]`
전제: operator가 bench에 상주, e-stop 파지, 팔 주변 이격 확보.
스크립트는 첫 모션 전 반드시 콘솔에서 명시적 확인 입력을 받는다
(confirm_first_motion 규칙과 동일 취지).

절차 (팔 1대 기준, cell6과 cell7 각각 별도 실행):

| 단계 | 동작 | 판정 |
|---|---|---|
| J1 | SDK 접속, joint 6축 읽기 `q0` | 통신 성공, 값 범위 정상 |
| J2 | joint 1 목표 = `q0[0] + 10.0deg`, 속도 `jog_speed_pct`로 MoveJ | 명령 수락 |
| J3 | 정지 대기 후 encoder 재독 `q1`, 그리고 **독립 경로로 한 번 더** `q1b` (예: 상태 조회 API를 별도 호출) | 두 읽기가 존재 |
| J4 | 증분 검증: `q1[0] - q0[0] = +10.0 ± 0.5 deg` | LearnedPatterns #33: endpoint가 아니라 이동량을 검증 |
| J5 | 독립성 검증: `q1`과 `q1b`의 joint 1 차이 기록. 완전 동일값 연속 일치 시 캐시 의심 플래그 | LearnedPatterns #34 |
| J6 | 타 축 불변 검증: joint 2~6 각각 `|Δ| ≤ 0.5 deg` | 의도치 않은 커플링 탐지 |
| J7 | 복귀: joint 1 목표 = `q0[0]`, MoveJ, 재독 `q2` | `q2[0] - q0[0] = 0 ± 0.5 deg` |
| J8 | J2~J7을 총 3회 반복 | 3/3 성공, 각 회 증분 오차 기록 |

산출: 실행 로그를 `claude_test/smoke_arm_<robot_id>_<UTC>.md`로 저장.
각 회의 q0, q1, 증분, 소요 시간, 두 독립 읽기의 차이를 표로 기록한다.

인수 조건: **cell6 팔과 cell7 팔 각각 3/3 통과.** 이 조건을 만족해야
T2로 진행한다.

주의: 이 테스트는 replay 경로를 쓰지 않고 SDK의 MoveJ를 직접 쓴다.
목적이 "encoder 기준으로 팔이 실제로 움직이고 그 값을 읽을 수 있는가"의
검증이기 때문이다. +10도가 관절 한계나 주변 구조물과 간섭하는 자세라면
operator 판단으로 시작 자세를 먼저 조정한 뒤 실행한다.

### T2. L1 서버 실기 (read-only부터)

각 cell 서버를 기동한 뒤 순서 고정:

1. `GET /v1/health`, `GET /v1/diagnose`: `arm.ok=true`, robot_id와 ip가
   config와 일치.
2. `GET /v1/status` 10회 연속: joint 값 일관, 오류 0회.
3. 타 action set 409 확인: `POST /v1/pump/initialize` → 409.
4. `POST /v1/arm/prefetch` (기존 검증된 dataset): `cached=true`, frames와
   fps가 기록과 일치.

### T3. replay E2E (팔 1대, 검증된 짧은 episode)

1. curl로 `POST /v1/arm/replay` 1회. 기존 `6__replay.sh`로 재생 성공
   이력이 있는 dataset과 episode를 사용한다.
2. 판정: `completed=true`, `final_joint_error_deg ≤ 1.0`, elapsed_s가
   `frames/fps`의 0.9~1.5배 이내.
3. **stop 실측**: replay 재실행 중 `POST /v1/stop`. 판정: 응답 2초 이내,
   subprocess 종료, 팔 정지, status 재독 가능. 결과를 `docs/L1_AUDIT.md`에
   기록한다 (이 cell은 GAP-9 비대상임을 수치로 증명).

### T4. L2 시나리오

1. `python -m orchestrator validate scenarios/demo_arm_replay.yaml` 0 issue.
2. step-mode 실행: prefetch → assert cached → replay → assert
   `final_joint_error_deg ≤ 1.0`. 첫 모션 step에서 operator confirm이
   걸리는지 확인.
3. orchestrator 코드 diff가 0줄임을 확인 (D1 검증).

시나리오 초안:

```yaml
name: demo_arm_replay
params:
  repo: "coport-uni/FR5_pick_red_colored_marker_to_box"
  ep: 0

steps:
  - id: check_arm
    cell: cell6
    action: diagnose
    method: GET
    save_as: diag
  - id: assert_ready
    assert: "${diag.arm.ok} == True"
  - id: prefetch
    cell: cell6
    action: arm/prefetch
    body: {repo_id: "${params.repo}", episode: "${params.ep}"}
    save_as: pre
  - id: assert_cached
    assert: "${pre.cached} == True"
  - id: run
    cell: cell6
    action: arm/replay
    body: {repo_id: "${params.repo}", episode: "${params.ep}"}
    save_as: rep
    timeout_s: 200.0
  - id: assert_arrived
    assert: "${rep.final_joint_error_deg} <= 1.0"
```

## 8. 안전 규칙 (테스트, 운영 공통)

1. 모든 모션은 operator 상주와 e-stop 가시권을 전제한다. 소프트웨어
   stop은 보조 수단이다.
2. `smoke_arm.py`와 replay의 첫 모션 전 확인 입력은 생략 플래그를 만들지
   않는다.
3. 두 팔이 같은 LAN에 오르기 전에 2호기 IP를 teach pendant에서 변경
   완료해야 한다. 미변경 상태로 동시 연결 금지.
4. 두 팔의 도달 범위 중첩이 실측으로 확인되기 전까지 cell6과 cell7을
   같은 parallel block에 넣는 시나리오를 작성하지 않는다 (GAP-8).
5. field 이름에 `on`, `off`, `yes`, `no`, `y`, `n`을 쓰지 않는다
   (YAML 1.1, LearnedPatterns #8).

## 9. Definition of Done

| # | 항목 |
|---|---|
| 1 | T0 전체 green, `ruff check cell/ server/ orchestrator/ claude_test/` 통과 |
| 2 | T1: 두 팔 각각 joint 1 +10도 3/3 통과, 증분 오차 ≤ 0.5 deg, 리포트 파일 2개 커밋 |
| 3 | T2: 두 cell 모두 read-only 통과, 409 매핑 확인 |
| 4 | T3: replay 1회 성공 + stop 실측 수치가 `docs/L1_AUDIT.md`에 기록 |
| 5 | T4: validate 0 issue, step-mode 완주, orchestrator diff 0줄 |
| 6 | `SUBMODULES.md`에 D3/D4 예외 사유 기록, README의 cell 표에 cell6/cell7 행 추가 |

## 10. 구현 전 확인 사항 (Claude Code가 코드 확인으로 해소할 것)

| # | 질문 | 확인 방법 |
|---|---|---|
| Q1 | fairino SDK가 cell 프로세스의 상태 읽기 연결과 replay subprocess의 제어 연결을 동시에 허용하는가 | `FR5ControllerVLA`의 fairino_follower 구현과 SDK 문서. 불허 시 status는 idle 한정으로 설계 변경 |
| Q2 | `lerobot-replay`가 실패 시 0이 아닌 종료 코드를 반환하는가 | 소스 확인 + T3에서 실측. 항상 0이면 최종 pose 검증을 성공 판정 기준으로 승격 |
| Q3 | fork의 replay가 첫 frame으로의 선행 이동을 자체 수행하는가 | 소스 확인. 수행한다면 6.2의 4단계는 중복이므로 tolerance 검증만 남긴다 |
| Q4 | dataset meta에서 total_episodes와 episode별 frame 수를 읽는 API | lerobot dataset 버전 확인. T0-3, T0-5의 mock 형태를 이 API에 맞춘다 |
| Q5 | SDK의 정지 명령 명칭과 동작 (StopMotion 계열, ServoMoveEnd 등) | SDK 확인 후 stop() 구현과 T0-9 mock에 반영 |
| Q6 | cell6, cell7의 소속 NUC와 2호기 IP, gripper 장착 여부 | 사용자 확인 필요. config example의 placeholder로 두고 TBD 표기 |
