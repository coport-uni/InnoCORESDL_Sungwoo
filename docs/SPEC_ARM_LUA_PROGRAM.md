# 개발사양서: FR5 Lua 잡 프로그램 실행 경로 (cell6, cell7)

대상 저장소: `coport-uni/InnoCORESDL_Sungwoo`
선행 문서: `docs/SPEC_ARM_REPLAY_CELL.md` (v1.0) — 본 문서는 그 확장이다
문서 버전: v1.0 (구현·검증 완료), 2026-08-11
실행 주체: Claude Code

> **검증 상태 (2026-08-11): 두 팔 모두 모션 검증 완료.**
>
> - §7 T0' 정찰 7/7 — `claude_test/probe_arm_lua_fr5_a_20260811T042141Z.md`
> - §7 T2 — `claude_test/bench_arm_program_20260811.md`. replay 경로를
>   삭제하고 `cell/arm_cell.py`로 개명한 **뒤에** 두 서버를 재시작해 다시
>   측정한 값이다 (모션 경로 리팩터는 살아남은 테스트로 검증되지 않는다):
>   - cell6 `Test1.lua`: `elapsed_s 23.786`, `last_line 18`, 폴트 없음
>   - cell7 `Cell7Test1.lua`: `elapsed_s 25.293`, `last_line 13`, 폴트 없음
> - **아직 안 한 것**: `POST /v1/stop` 실측(§7 T2-2), 시나리오 step-mode
>   완주(§7 T3). operator 게이트가 콘솔 입력을 요구하므로 시나리오는
>   운영자가 직접 돌려야 한다.
>
> 이 문서의 초안은 코드와 매뉴얼만 읽고 쓴 것이었고, 벤치가 그중 **세 가지를
> 뒤집었다** — §4.1(래퍼가 죽지 않는다), §4.6(수락-진입 간극),
> §4.6(라인 리셋). 정정 내용은 각 절에 그대로 남겨 둔다.

근거 문헌:

- [FR Lua Programming Script User Manual V1.0](https://fairino.support/download/FR%20Lua%20Programming%20Script%20User%20Manual-V1.0.pdf)
  (소프트웨어 v3.7.4 기준, 152쪽) — 이하 **[Lua매뉴얼]**
- [SDK Manual, WebAPP program use](https://fairino-doc-en.readthedocs.io/3.7.0/SDKManual/PythonRobotWebAPPProgramUse.html)
  — 이하 **[SDK매뉴얼]**
- `external/FR5Controller/fairino/Robot.py` (vendored SDK, 14436줄)
- `external/FR5Controller/fairino/example/TestWebAppCommand.py` (벤더 예제)

---

## 1. 목적

이 문서를 쓰기 시작한 시점에 cell6/cell7의 유일한 모션 수단은 lerobot
replay였다 (`SPEC_ARM_REPLAY_CELL.md` §1). 그 경로는 PC가 dataset episode를
열어 20 Hz로 `ServoJ` 프레임을 컨트롤러에 계속 밀어넣는 구조여서,

- **PC와 네트워크가 에피소드 전 구간 동안 실시간 루프에 묶인다.**
- **녹화된 episode가 없는 동작은 표현할 수 없다.** 새 동작 하나를 추가하려면
  teleop으로 녹화하고 HuggingFace에 올려야 한다.

Fairino 펌웨어는 이미 컨트롤러 자체 스크립트 실행기 겸 모션 플래너를 갖고
있다. `.lua` 잡 프로그램을 컨트롤러가 직접 해석·보간하며, PC는 실행 명령
한 번만 보내면 된다. 본 사양은 **이미 WebApp/티치펜던트로 작성되어 컨트롤러에
올라가 있는 Lua 프로그램을 L1 `/v1` API로 load / run / stop 하고 진행 상태를
관측하는 경로**를 정의한다.

**최종 결과 (2026-08-11): 이 경로가 replay를 대체했다.** 초안은 두 경로의
공존을 전제했고 실제로 그렇게 구현·검증했지만, 두 팔에서 동작을 확인한 뒤
사용자 판단으로 replay를 리포에서 제거했다. 이유와 — 더 중요하게 —
replay가 더 나았던 점은 `LearnedPatterns.md` #47에 있다. 요약하면 학습된
정책 실행은 replay 계열만 할 수 있고(정책은 포즈를 연속 생성하므로 컨트롤러에
미리 올릴 것이 없다) 종료 검증도 더 강하다. VLA 롤아웃이 다시 범위에 들어오면
`docs/SPEC_ARM_REPLAY_CELL.md`와 git 히스토리에서 되살린다.

아래 §1.1의 비교표와 §3의 L1/L4/L7/L8은 **두 경로가 공존하던 시점의
기록**이다. 그 대비가 왜 이 경로를 택했는지를 설명하므로 남겨 둔다.

### 1.1 두 경로의 성질 비교

| | replay (`arm/replay`) | Lua 프로그램 (`arm/program`) |
|---|---|---|
| 모션 계획 주체 | PC (lerobot이 프레임 재생) | **컨트롤러 펌웨어** |
| 전송 | `ServoJ` 20 Hz 스트림 | 명령 1회 (`ProgramRun`) |
| PC 장애 시 | 스트림 끊김 → 팔 정지 | 컨트롤러가 계속 실행 |
| 동작 정의 위치 | HuggingFace dataset | 컨트롤러의 `/fruser/*.lua` |
| 보간·블렌딩 | 없음 (프레임 그대로) | `blendT` [0~500] ms / `blendR` [0~1000] mm |
| 진행 피드백 | 프레임 단위 (암묵) | `GetCurrentLine()` 폴링만 |
| 종료 검증 | 마지막 frame 대비 축 오차 | **불가** (§6.4 참조) |
| VLA 정책 실행 | 가능 | 불가 |

### 1.2 Lua가 제공하는 것 ([Lua매뉴얼] 목차 기준)

| 절 | 내용 |
|---|---|
| §3.2 Motion command | `PTP`, `MoveJ`, `Lin`, `MoveL`, `ARC`, `MoveC`, Spiral, Spline, Swing, Trajectory Reproduction, Servo, TrajectoryJ, DMP, 공작물/공구 좌표 변환 |
| §3.3 Control instruction | 디지털/아날로그/가상 IO, 좌표계, 모드 전환, 충돌 레벨 |
| §3.4 Peripheral | 그리퍼, 스프레이건, 확장축, 컨베이어, 연마기 |
| §3.5 Welding | 용접, 아크/레이저 트래킹 |
| §3.6 Force Control | 힘 제어, 토크 기록 |
| §3.7 Communication | Modbus |
| §3.8 Auxiliary | 보조 스레드, 함수 호출, **포인트 테이블** |

즉 "SDL 워크플로 한 스텝"에 해당하는 동작(집기 → 이동 → 놓기 → IO 토글)을
Lua 하나로 표현할 수 있고, 포인트 테이블(§3.8.3, SDK의
`PointTableSwitch` / `PointTableUpdateLua`)로 좌표만 갈아끼울 수도 있다.

## 2. 범위

포함:

- `cell/arm_cell.py`의 program action set (초안 시점에는
  `cell/arm_replay_cell.py`에 **추가**하는 형태였다 — §3 L1/L2)
- `server/` 라우트·스키마·에러 매핑
- `server/nuc2/cell6.toml.example`, `server/nuc1/cell7.toml.example`의
  `[arm]` 테이블 확장
- `claude_test/probe_arm_lua.py` (무모션 정찰), 단위 테스트 확장
- `scenarios/demo_arm_program.yaml`
- `LearnedPatterns.md` 신규 항목 (§10)

제외 (비범위):

- **컨트롤러 파일시스템 쓰기.** `LuaUpload`, `LuaDelete`, `AxleLuaUpload`,
  `OpenLuaUpload`, `TrajectoryJUpLoad`는 구현하지 않는다. 프로그램은
  WebApp/티치펜던트로 작성·배포하고, L1은 실행만 한다.
- Lua 스크립트 자체의 저술. 리포에 `.lua`를 두지 않는다.
- `LoadDefaultProgConfig` (부팅 시 자동 로드 설정). 무인 기동 모션은
  `L2_ORCHESTRATOR_SPEC.md`의 "무인 모션 금지" 원칙과 정면 충돌한다.
- TPD / `MoveTPD` / `LoadTrajectoryJ` 계열. 별개 기능이며 본 사양 대상 아님.
- L2 orchestrator 코드 변경. **변경량 0이 정상이다** — `arm/`이 이미
  `orchestrator/registry.py`의 `DEFAULT_HAZARD_PREFIXES`에 있으므로
  `arm/program`은 자동으로 operator 게이트를 탄다.

## 3. 아키텍처 결정

| ID | 결정 | 근거 |
|---|---|---|
| L1 | replay cell에 action set을 **추가**한다. 별도 `ArmLuaCell`을 만들지 않는다 | `_read_joints`, `prepare_arm`, `_require_ready`, `stop()`, 타 shape 409 스텁 22개가 전부 공유된다. 분리하면 통째로 복제된다. 한 서버가 두 경로를 모두 제공해야 시나리오에서 섞어 쓸 수 있다 |
| L2 | 파일·클래스를 `cell/arm_cell.py` / `ArmCell`로 개명한다 | `ArmReplayCell`이 더 이상 사실이 아니다. **적용됨** — replay 제거와 함께 개명, 1940 → 1254줄 |
| L3 | 모든 Lua API 호출은 **raw `rpc.robot.*`** 로 한다. SDK 래퍼를 쓰지 않는다 | §4에 근거. 셀이 이미 `GetActualJointPosDegree` / `MoveJ` / `StopMotion`에서 지키는 규칙과 동일 |
| L4 | 실행은 `start_program` + `await_program` 2단계. 라우트는 `arm/replay`와 **동일한 비대칭 락 패턴** | 락 안에서 시작, 락 밖에서 대기 → 실행 중 `POST /v1/stop`이 모션 뒤에 줄 서지 않는다 (GAP-9 회피) |
| L5 | 프로그램 이름은 요청 body의 변수. 단 `.lua` 확장자 + 경로 구분자 금지 + config 화이트리스트 검사 | `allowed_repo_prefixes`(D6)와 같은 취지. 경로 탈출(`../`) 차단 |
| L6 | timeout은 config 상수 `max_program_s`. replay처럼 계산하지 않는다 | frames/fps에 해당하는 사전 정보가 없다. 프로그램 길이를 L1이 알 방법이 없다 |
| L7 | 프로그램 실행 중에는 replay를 띄우지 않고, replay 중에는 프로그램을 실행하지 않는다. 둘 다 409 | 서보 세션과 잡 프로그램은 모션의 소유자가 서로 다르다 (§4.3). replay 제거 후에는 "프로그램은 한 번에 하나"만 남았다 |
| L8 | `stop()`은 subprocess → `ProgramStop()` → `StopMotion()` 순서. 어느 단계도 예외를 밖으로 내지 않는다 | e-stop 성격의 호출은 절대 raise 하지 않는다. replay 제거 후 subprocess 단계가 빠져 2단계다 |

## 4. 사전 확인된 SDK 함정 (코드 실사 결과)

구현 전에 반드시 알고 있어야 하는 것들. 전부 `Robot.py`를 직접 읽어 확인했다.

### 4.0 Phase-0 정찰 실측값 (cell6, 2026-08-11)

`claude_test/probe_arm_lua.py --ip 192.168.0.58 --robot-id fr5_a`, 7/7 응답:

| 호출 | 반환 (raw) |
|---|---|
| `GetLuaList()` | `[0, 7, 'test.lua;example.lua;new_pr.lua;SimpleLoadIdentify.lua;Test0902.lua;Test1.lua;test2.lua;']` |
| `GetLoadedProgram()` | `[0, '/fruser/test2.lua']` |
| `GetProgramState()` (raw) | `[0, 1]` |
| `GetProgramState()` (래퍼) | `(0, 1)` |
| `GetCurrentLine()` | `[0, 0]` |
| `GetRobotErrorCode()` | `[0, 0, 0]` |
| `GetActualJointPosDegree(1)` | `[0, -137.755, -99.876, 95.266, -87.985, -89.388, 2.914]` |

확정된 것:

- `GetLuaList()`는 `(error, count, "a;b;c;")` 형태가 맞다 (Q2 해소). 끝에
  빈 항목이 남는 후행 `;`가 있으므로 split 후 빈 문자열을 걸러야 한다.
- raw `GetProgramState()`는 `(error, state)`가 맞다 (Q1 해소).
- **프로그램 이름은 대소문자를 구분한다.** 커미셔닝 대상은
  `Test1.lua`이며 소문자 `test1.lua`는 이 컨트롤러에 없다 (Q3 해소).

### 4.1 `GetProgramState()` 래퍼는 프로그램 상태를 읽지 않는다

> **정정 (2026-08-11).** 이 절의 초안은 "cell6에서 래퍼가
> `TypeError: '_ctypes.CField' object is not subscriptable`로 죽는다"고
>썼다. **틀렸다.** 실측에서 래퍼는 `(0, 1)`을 정상 반환했다 — 이 컨트롤러의
> port-20004 스트림은 살아 있다. 아래 근거는 "죽는다"가 아니라 "다른 필드를
> 읽는다"로 좁혀진다. 값이 우연히 raw와 같은 `1`이었다는 것은 두 값이 같은
> 것을 뜻한다는 증거가 아니다.


`Robot.py:4915-4925`:

```python
def GetProgramState(self):
    # _error = self.robot.GetProgramState()
    # error = _error[0]
    # if error == 0:
    #     return error, _error[1]
    # else:
    #     return error
    return 0, self.robot_state_pkg.robot_state
```

XMLRPC 본문이 통째로 주석 처리되어 있고, 대신 port-20004 실시간 상태
구조체의 `robot_state` 필드를 반환한다. 문제는 세 가지다:

1. **읽는 대상이 다르다.** `robot_state`는 로봇의 운전 상태이지 잡 프로그램의
   실행 상태가 아니다. 실측에서 둘 다 `1`이었지만, 프로그램이 정지해 있고
   로봇도 정지해 있는 상태에서 두 값이 같았다는 것은 아무것도 증명하지
   않는다. 폴링 루프가 판정에 쓸 값으로는 부적격이다.
2. **실패를 보고할 수 없다.** `return 0, ...`으로 성공 코드를 하드코딩한다 —
   LearnedPatterns #15가 모션 경로에서 걷어낸 "조작된 성공"과 같은 형태.
3. **20004가 두절되면 죽는다.** 구조체가 ctypes 클래스인 채로 남아
   `TypeError: '_ctypes.CField' object is not subscriptable`이 난다
   (LearnedPatterns #40). cell6에서는 2026-08-11 현재 20004가 살아 있어
   이 경로가 터지지 않았지만, 그것은 스트림 상태에 달린 우연이다.

→ 반드시 raw `rpc.robot.GetProgramState()`를 쓴다. 실측으로 `[0, 1]` 형태가
확인되었다 (§4.0).

### 4.2 Program 계열 래퍼에 무한 스핀이 있다

`ProgramLoad`, `ProgramRun`, `ProgramPause`, `ProgramResume`, `ProgramStop`,
`GetCurrentLine`, `GetLoadedProgram`, `LoadDefaultProgConfig` 전부 상단이
이렇게 시작한다:

```python
while self.reconnect_flag:
    time.sleep(0.1)
```

`reconnect_flag`는 SDK 상태 스레드가 20004 스트림 두절 시 래치하는 **클래스**
속성이고, 한 번 래치되면 풀리지 않는다 — LearnedPatterns #41,
`POST /v1/stop`이 끝나지 않던 그 버그와 같은 것. 이 벤치에서 20004가 지금
살아 있다는 것(§4.0)은 앞으로도 그렇다는 뜻이 아니고, e-stop 성격의 호출을
스트림 상태에 의존하게 둘 이유가 없다.
`ProgramRun`과 `ProgramResume`은 추가로 `GetSafetyCode()`를 부르는데, 그것도
같은 죽은 구조체를 읽는다.

→ 전부 raw `rpc.robot.*`로 우회. raw 호출은 소켓 타임아웃으로 유계다.

### 4.3 서보 세션과 잡 프로그램은 모션 소유자가 다르다

`FR5ControllerVLA`의 `FairinoFollower.connect()`는 `Mode(0)` 후
`ServoMoveStart()`로 서보 세션을 연다. 잡 프로그램이 도는 동안 서보 세션이
살아 있으면 두 주체가 같은 축을 지시하게 된다.

→ `start_replay`가 spawn 직전 `_release_rpc()`로 세션을 넘기는 것과 대칭으로,
프로그램 실행과 replay는 상호 배타여야 한다 (L7). replay subprocess가 살아
있는 동안 `start_program`은 409.

### 4.4 `Mode(0)`(자동 모드)가 `ProgramRun`의 전제조건이다

[SDK매뉴얼]의 예제와 `TestWebAppCommand.py` 모두 `robot.Mode(0)` →
`ProgramLoad` → `ProgramRun` 순서다. 셀의 `prepare_arm()`이 이미
`ResetAllError` → `RobotEnable(1)` → `Mode(0)`을 하므로 재사용 가능하지만,
`start_program`은 그 사이에 모드가 바뀌었을 수 있으므로 `Mode(0)`을 자기
시퀀스 안에서 한 번 더 호출한다.

### 4.5 `/fruser`는 고정 경로다

[SDK매뉴얼]과 `Robot.py:4780` 주석 모두 `"/fruser/movej.lua"` 형태를 쓰며
"`/fruser/`는 고정 경로"라고 명시한다. 궤적 파일은 `/fruser/traj/*.txt`
(`Robot.py:4478`). 업로드/다운로드는 FTP도 웹 UI도 아닌 전용 TCP
프로토콜(업로드 20010 / 다운로드 20011, MD5 검증 `/f/b … /b/f` 프레이밍,
`Robot.py:7115-7235`)이지만 — **본 사양의 비범위다** (§2).

### 4.6 `ProgramRun`은 수락 시점에 답하고, `GetCurrentLine`은 끝에서 0으로 리셋된다

이 두 가지가 첫 실기 실행에서 **실제 결함 2건**을 만들었다. 전말은
LearnedPatterns #46, 측정 데이터는 아래.

`/fruser/Test1.lua`, cell6, 2026-08-11:

```
run      -> 0
  t+  0.001s  state=1  line=0     ← 수락. 아직 실행 아님
  t+  0.160s  state=2  line=4     ← 여기서 비로소 실행 상태
  t+  5.643s  state=2  line=9
  t+ 11.064s  state=2  line=11
  t+ 14.332s  state=2  line=14
  t+ 17.388s  state=2  line=18
  t+ 23.712s  state=2  line=0     ← 종료하면서 line이 0으로 리셋
  t+ 23.764s  state=1  line=0
```

1. **160 ms의 수락-진입 간극.** 그 안에서 폴링하면 "정지"를 보고 "끝났다"고
   판정한다. 첫 `POST /v1/arm/program`이 정확히 그랬다 — 2.8 ms 만에
   `completed: true`를 반환했고, 그 뒤 팔이 joint 1을 91.27° 돌렸다. 이
   파일이 MoveJ에 대해 이미 `JOG_SETTLE_S`로 흡수하고 있는 바로 그 간극이다.
   → `_confirm_started()`: 50 ms 간격으로 상태가 1을 벗어날 때까지 폴링,
   상한 `PROGRAM_START_GRACE_S = 5.0`(측정치의 ~30배), 끝내 안 벗어나면
   `DeviceFaultError`.
2. **`last_line`은 최대값이어야 한다.** 마지막 값은 항상 0이다. 게다가 이
   프로그램은 시작 자세로 되돌아오므로(종료 자세가 초기 대비 0.004° 이내)
   자세 변화로도 실행을 증명할 수 없다 — `last_line > 0`이 유일한 증거다.

수정 후 재측정: `elapsed_s = 23.789 s`, `last_line = 18`, HTTP 벽시계
23.803 s. 즉 응답이 프로그램 종료와 함께 도착한다.

## 5. Config 스키마 (증분)

기존 `[arm]` 테이블에 4개 키를 추가한다. 나머지는
`SPEC_ARM_REPLAY_CELL.md` §5 그대로.

```toml
# server/nuc2/cell6.toml.example  (cell7도 동일 키)
[arm]
# ... 기존 replay 키 13개 ...

program_dir = "/fruser"        # 컨트롤러의 Lua 고정 경로 (§4.5). 변경 금지에 가깝다
allowed_programs = []          # 빈 배열이면 임의 *.lua 허용, 채우면 화이트리스트
max_program_s = 300.0          # 프로그램 실행 timeout 상한 (L6)
program_poll_s = 0.5           # GetProgramState 폴링 주기
```

`ArmReplayConfig.from_toml`의 타입 강제 패턴을 그대로 따른다 (TOML은
`300`을 int로 주므로 float 필드는 명시적으로 캐스팅해야 하고, 기존 T0-1이
이를 검사한다).

`allowed_programs`를 비워두는 것이 기본값인 이유: 컨트롤러에 어떤 프로그램이
있는지 리포가 모른다 (WebApp으로 만들어졌으므로). §7 T0' 정찰로 목록을
확보한 뒤 화이트리스트를 채우는 것을 권장한다.

## 6. Cell / API 사양

### 6.1 program action set

| Route | Method | Body | 성공 응답 (요지) |
|---|---|---|---|
| `arm/program` | POST | `{name: "x.lua"}` | `{completed: true, name, elapsed_s, last_line, joints_deg: [...]}` |

`arm/programs`(GET, `GetLuaList()`) 는 **구현하지 않았다.** 사용자가 고른
"실행만" 범위를 넘고, 이름 발견은 `claude_test/probe_arm_lua.py`가 셀 서버
없이 해준다. 필요해지면 읽기 전용 라우트로 추가하면 된다 (Q7).

`last_line`은 **관측된 최대 라인**이지 마지막 값이 아니다. 컨트롤러가 종료
시 `GetCurrentLine`을 0으로 되돌리기 때문이다 (§4.6). 시나리오가
"스크립트가 실제로 실행됐다"를 주장할 수 있는 유일한 근거이므로 이 값이
0이면 실행되지 않은 것으로 읽어야 한다.

### 6.2 `arm/program` 실행 시퀀스 (순서 고정)

1. `_replay_lock` 비블로킹 획득 실패 → 409.
2. replay subprocess가 살아 있으면 → 409 (L7).
3. raw `GetProgramState()`가 2(실행) 또는 3(일시정지)이면 → 409.
4. 이름 검증: `.lua`로 끝나야 하고 `/`, `\`, `..`이 있으면 400.
   `allowed_programs`가 비어있지 않은데 목록에 없으면 400 (L5).
5. `_require_ready()` — 폴트 래치 상태면 409 (`POST /v1/arm/enable` 안내).
6. raw `Mode(0)` (§4.4).
7. raw `ProgramLoad(f"{program_dir}/{name}")`. 비-0 반환 → 500.
8. raw `GetLoadedProgram()`으로 **로드된 이름을 대조.** 불일치 → 500.
   `ProgramLoad`가 0을 반환했다는 것이 원하는 파일이 올라갔다는 뜻은 아니다.
9. raw `ProgramRun()`. 비-0 반환 → 500.
10. (여기까지가 락 안. 이하 락 밖.) `program_poll_s` 간격으로 raw
    `GetProgramState()` 폴링. 2/3이 아니게 되면 종료. 매 폴에서
    `GetCurrentLine()`을 읽어 `last_line`을 갱신한다.
11. `max_program_s` 초과 → raw `ProgramStop()` 후 504.
12. 종료 후 `GetRobotErrorCode()`가 `(0, 0)`이 아니면 → 500.
13. **encoder 재독**: `_read_joints()`로 joint를 실제로 다시 읽어 응답에
    포함. LearnedPatterns #24: 200 OK는 encoder를 읽었다는 뜻이어야 한다.

일시정지(state 3)에서 멈춰 있는 것은 완료가 아니다. 11의 timeout까지
기다린 뒤 `ProgramStop()`으로 끝낸다.

### 6.3 `stop()` 확장 (L8)

반환 dict가 `{"subprocess": ..., "sdk": ...}`에서
`{"subprocess": ..., "program": ..., "sdk": ...}`로 넓어진다. 순서:

1. replay subprocess SIGTERM → 2초 → SIGKILL
2. raw `ProgramStop()`
3. raw `StopMotion()`

기존 계약을 그대로 유지한다: **락을 잡지 않고**, 세 단계 중 어느 것도 예외를
밖으로 내지 않으며, 부분 실패는 문자열로 기록해 반환한다. 순서가 중요한
이유는 replay 때와 같다 — 상위 스트림/실행기를 먼저 죽여야 `StopMotion`이
다음 프레임/다음 줄에 되살아나지 않는다.

`server/schemas.py`의 `StopResponse.detail`은 이미 `dict | None`이므로
스키마 변경이 필요 없다.

### 6.4 검증이 replay보다 약하다 (명시 사항)

replay는 `final_joint_error_deg`(마지막 frame 대비 최대 축 오차)로 "의도한
자세로 끝났는가"를 수치로 답한다. Lua 프로그램에는 그에 해당하는 것이 없다 —
L1은 프로그램의 최종 목표점을 모르기 때문이다 (스크립트를 파싱하지 않는 한).

따라서 `arm/program`의 200은 다음 세 가지의 결합으로만 정의된다:

1. 프로그램 상태가 1(정지)로 복귀했다
2. 종료 시점에 폴트가 래치되지 않았다
3. 종료 후 encoder를 실제로 읽는 데 성공했고, 그 값을 응답에 담았다

**"의도한 위치에 도달했다"는 주장은 하지 않는다.** 그 판정은 시나리오
작성자가 `joints_deg`에 대한 assert로 직접 해야 한다. PR 본문과 API
docstring에 이 한계를 명시한다.

### 6.5 에러 매핑 (`CellError` 체계 준수)

| 상황 | 예외 | HTTP |
|---|---|---|
| 확장자 아님, 경로 구분자 포함, 화이트리스트 밖 | `InvalidArgError` | 400 |
| 프로그램/replay 실행 중 재호출, 폴트 래치 상태 | `WrongStateError` | 409 |
| `ProgramLoad`/`ProgramRun` 비-0, 로드된 이름 불일치, 종료 후 폴트 | `DeviceFaultError` | 500 |
| 컨트롤러 TCP 단절 | `TransportError` | 503 |
| `max_program_s` 초과 | `CellTimeoutError` | 504 |

## 7. TDD 구성

### T0'. Phase-0 무모션 정찰 (최우선, 하드웨어 필요하지만 모션 없음)

파일: `claude_test/probe_arm_lua.py`
실행: `python claude_test/probe_arm_lua.py --ip 192.168.0.59 --robot-id fr5_b`

읽기 전용. `Mode()`, `ProgramLoad`, `ProgramRun`, `MoveJ`를 **호출하지
않는다.** 각 getter를 개별적으로 감싸 하나가 실패해도 나머지 정찰 결과가
남게 한다. 셀 서버는 먼저 내린다 (규칙 #2 — TCP 세션도 소유자는 하나).

| # | 호출 | 알아내려는 것 |
|---|---|---|
| P1 | raw `GetLuaList()` | 컨트롤러에 실제로 있는 프로그램 이름과 **반환 튜플의 실제 형태** |
| P2 | raw `GetLoadedProgram()` | 현재 로드된 프로그램 |
| P3 | raw `GetProgramState()` | §4.1의 가정 검증. `(0, 1)` 형태가 맞는가 |
| P4 | **래퍼** `GetProgramState()` | §4.1의 반례 확보 (실패하거나 죽은 구조체를 주는 것을 기록) |
| P5 | raw `GetCurrentLine()` | 진행 채널이 응답하는가 |
| P6 | raw `GetRobotErrorCode()` | 폴트 래치 여부 |
| P7 | raw `GetActualJointPosDegree(1)` | 세션이 살아 있는가 |

산출: `claude_test/probe_arm_lua_<robot_id>_<UTC>.md`에 각 호출의 **raw
repr**을 그대로 기록. **이 결과 없이 §5의 파라미터와 §6.2의 폴링 로직을
확정하지 않는다.**

### T0. 단위 테스트 (하드웨어 불요)

파일: `claude_test/test_arm_replay_cell.py` 확장. `FakeProxy`에 `Mode`,
`ProgramLoad`, `ProgramRun`, `ProgramStop`, `GetProgramState`,
`GetCurrentLine`, `GetLoadedProgram`, `GetLuaList`를 추가하고, `FakeRPC`에
`program_state` 시퀀스 / `loaded_name` / `lua_list` 노브를 단다.

| # | 케이스 | 기대 |
|---|---|---|
| L0-1 | config 파싱: 신규 4키가 example TOML에서 올바른 타입으로 | 타입 일치 |
| L0-2 | `"../../etc/passwd"`, `"a/b.lua"`, `"x.txt"` | `InvalidArgError` |
| L0-3 | `allowed_programs=["ok.lua"]`에서 `"other.lua"` | `InvalidArgError` |
| L0-4 | replay 실행 중 `start_program` / 프로그램 실행 중 `start_replay` | 양방향 `WrongStateError` |
| L0-5 | 컨트롤러 상태가 2인데 `start_program` | `WrongStateError` |
| L0-6 | `ProgramLoad`는 0인데 `GetLoadedProgram`이 다른 이름 | `DeviceFaultError` |
| L0-7 | 호출 순서: `Mode(0)`이 `ProgramRun` **앞**에 | 호출 순서 assert |
| L0-8 | 상태가 계속 2 → `max_program_s` 초과 | `ProgramStop` 호출 + `CellTimeoutError` |
| L0-9 | 정상 종료 후 `GetRobotErrorCode`가 `(14, 0)` | `DeviceFaultError` |
| L0-10 | `stop()` 순서 subprocess → program → StopMotion, 각 단계 실패해도 미전파 | 순서와 반환 dict |
| L0-11 | 정상 완주 | `completed=True`, `last_line` 기록, `joints_deg`가 encoder 재독값 |

기존 `test_other_action_sets_are_409` 파라미터 목록에 신규 액션을 반영한다.

**기존 테스트의 알려진 문제:** `jog_joint`가 `JOG_SETTLE_S=2.0`,
`prepare_arm`이 2.5초를 실제로 sleep 하고 아무도 monkeypatch 하지 않아
현재 슈트가 ~16초 걸린다. 폴링 루프는 같은 실수를 반복하지 않도록
`program_poll_s`를 테스트에서 0으로 낮출 수 있게 설계한다.

완료 기준: `pytest claude_test` green, `ruff check` + `ruff format --check`
통과.

### T1. L1 서버 read-only 실기

1. `GET /v1/diagnose`: `program` 블록이 나오고 `running=false`.
2. `GET /v1/arm/programs`: T0'에서 확인한 목록과 일치.
3. 타 action set 409 유지 확인.

### T2. 프로그램 E2E (operator 입회, 팔 1대)

전제: operator가 bench 상주, e-stop 파지, 팔 도달 범위 이격 확보.
**첫 실행은 티치펜던트로 이미 돌려본 짧고 이동량이 작은 프로그램 하나로만.**

1. `POST /v1/arm/program` 1회. 판정: `completed=true`, `last_line`이
   실행 중 증가했다, 종료 후 상태 1 복귀, 폴트 없음.
2. **stop 실측**: 재실행 중 `POST /v1/stop`. 판정: 응답 2초 이내,
   프로그램 즉시 정지, 팔 정지, status 재독 가능. 수치를
   `docs/L1_AUDIT.md`에 기록한다.
3. cell7(fr5_b) 먼저, 그다음 cell6(fr5_a). cell6은 폴트 이력이 있다.

### T3. L2 시나리오

`python -m orchestrator validate scenarios/demo_arm_program.yaml` 0 issue,
step-mode 완주, **orchestrator diff 0줄** 확인.

```yaml
name: demo_arm_program
params:
  arm: cell6
  program: "TBD.lua"          # T0' 정찰 결과로 채운다

steps:
  - id: check_arm
    cell: cell6
    action: diagnose
    method: GET
    save_as: diag
  - id: assert_ready
    assert: "${diag.arm.ready} == True"
  - id: enable
    cell: cell6
    action: arm/enable
    save_as: en
  - id: assert_enabled
    assert: "${en.fault_cleared} == True"
  - id: run
    cell: cell6
    action: arm/program
    body: {name: "${params.program}"}
    save_as: prog
    timeout_s: 360.0          # cell의 max_program_s(300)보다 크게 —
                              # cell의 timeout이 먼저 걸려 ProgramStop 하도록
  - id: assert_done
    assert: "${prog.completed} == True"
```

`assert:`는 값을 **따옴표 없이** 보간하므로 문자열 비교를 쓰지 않는다
(기존 arm 시나리오와 동일한 제약).

## 8. 안전 규칙

1. **접근 가드가 없다는 것이 이 경로의 최대 위험이다.** replay는 첫 frame과
   30° 이상 벌어져 있으면 거부한다 (`_approach_start`,
   `MAX_START_APPROACH_DEG`). Lua 프로그램의 첫 `PTP`/`MoveJ`는 **현재
   자세가 어디든 스크립트가 적은 `ovl` 속도로 티칭 포인트까지 컨트롤러가
   알아서 계획해서 간다.** L1은 그 목표점을 모른다. 없앨 수 없는 성질이므로
   `start_program`의 docstring, 라우트 summary, 시나리오 헤더 세 곳에
   명시한다.
2. 모든 모션은 operator 상주와 e-stop 가시권을 전제한다. 소프트웨어 stop은
   보조 수단이다.
3. `arm/program`은 `DEFAULT_HAZARD_PREFIXES`의 `arm/`에 걸려 L2에서
   operator confirm을 받는다. 이 게이트를 우회하는 코드를 쓰지 않는다.
4. 두 팔의 도달 범위 중첩이 실측되기 전까지 cell6과 cell7을 같은 parallel
   block에 넣지 않는다 (GAP-8).
5. `LoadDefaultProgConfig`(부팅 자동 실행)는 구현하지 않는다 (§2).
6. field 이름에 `on`/`off`/`yes`/`no`/`y`/`n`을 쓰지 않는다 (YAML 1.1,
   LearnedPatterns #8).

## 9. Definition of Done

| # | 항목 | 상태 |
|---|---|---|
| 1 | T0' 정찰 리포트 커밋, §4.1 가정이 실측으로 확인 또는 반증됨 | **완료** — 반증됨, §4.1 정정 |
| 2 | T0 전체 green, `ruff check` + `ruff format --check` 통과 | **완료** — 102 passed |
| 3 | T1 read-only 통과 (`diagnose.program`, 400 게이트 4종) | **완료** |
| 4 | T2: 팔 1대에서 프로그램 완주 | **완료** — cell6, 23.789 s / line 18 |
| 5 | T2-2: `POST /v1/stop` 실측 수치가 `docs/L1_AUDIT.md`에 기록 | **미완** |
| 6 | T3: validate 0 issue / step-mode 완주 / orchestrator diff 0줄 | validate·diff는 완료, step-mode **미완** (운영자 게이트) |
| 7 | cell7(fr5_b)에서 T2 반복 | **완료** — `Cell7Test1.lua`, 25.298 s / line 13 |
| 8 | `LearnedPatterns.md` 항목 (Problem/Cause/Fix/Rule) | **완료** — #46 |
| 9 | `README.md` arm 절에 두 경로의 차이와 선택 기준 | **미완** |
| 10 | PR 본문에 §6.4(검증이 replay보다 약함)를 명시. 낙관적 요약은 결함 | 머지 시 |

## 10. 구현 전 확인 사항

| # | 질문 | 확인 방법 | 상태 |
|---|---|---|---|
| Q1 | raw `GetProgramState()`가 정말 `(error, state)`를 반환하는가 | T0' P3 | **확인** — `[0, 1]` (§4.0) |
| Q2 | `GetLuaList()` raw 반환이 `(error, count, "a;b;c")` 형태가 맞는가 | T0' P1 | **확인** — 후행 `;` 주의 (§4.0) |
| Q3 | 컨트롤러에 실제로 있는 프로그램 이름 | T0' P1 | **확인** — 7개, 대상은 `Test1.lua` (§4.0) |
| Q4 | 프로그램 실행 중 다른 XMLRPC getter(`GetActualJointPosDegree`)가 응답하는가 — `status()`가 실행 중 무엇을 반환할지가 여기 달렸다 | T2에서 실측. 불응하면 replay와 같이 캐시값을 서빙 | **미확인** |
| Q5 | `ProgramStop()` 후 팔이 즉시 서는가, 아니면 현재 줄을 끝내는가 | T2 stop 실측 | **미확인** |
| Q6 | L2 결정 — 파일/클래스를 `arm_cell.py` / `ArmCell`로 개명할 것인가 | 사용자 확인 필요 | **미결정** |
| Q7 | `list_programs` / `arm/programs`를 넣을 것인가 (사용자가 고른 "실행만" 범위를 한 뼘 넘는다) | 사용자 확인 필요 | **미결정** |
