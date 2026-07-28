# Deployment — two NUCs, one repository

Both NUCs clone **this same repository at the same commit**. There is no
per-NUC branch, fork, or code copy (spec section 3). The only differences
are which config files exist and which systemd instances are enabled.

| Process | Where | How | Why |
|---|---|---|---|
| L1 cell servers | each NUC's **host** | `systemd` template unit | they own USB serial handles; the container's `/dev` is a private tmpfs that goes stale on re-enumeration |
| L2 orchestrator | **NUC1 only** | Docker Compose | no hardware access; restart policy + reproducible image |

Exactly **one** orchestrator exists SDL-wide. Two orchestrators sharing a
cell is a known source of instability (HELAO) and is banned by design.

## 1. Both NUCs — one-time setup

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone <repo-url> InnoCORESDL_Sungwoo && cd InnoCORESDL_Sungwoo
git submodule update --init --recursive

conda create -n sdl python=3.12 -y && conda activate sdl
pip install -r requirements.txt

sudo usermod -aG dialout "$USER"     # CH340 / FTDI / CDC serial nodes
```

Confirm both machines agree before every run:

```bash
git rev-parse HEAD                   # must match on NUC1 and NUC2
```

## 2. Cell servers (both NUCs)

Copy the examples for the cells this NUC owns and fill in the real device
identifiers (`*.toml` is gitignored; `*.toml.example` is tracked):

```bash
cp server/nuc1/cell1.toml.example server/nuc1/cell1.toml   # NUC1
cp server/nuc1/cell4.toml.example server/nuc1/cell4.toml   # NUC1
cp server/nuc2/cell2.toml.example server/nuc2/cell2.toml   # NUC2
cp server/nuc2/cell3.toml.example server/nuc2/cell3.toml   # NUC2
cp server/nuc2/cell5.toml.example server/nuc2/cell5.toml   # NUC2 (Cell D)
```

Install the template unit and enable the instances for **that** NUC — the
instance name after `cell@` is the config path with `/` written as `-`:

```bash
sudo cp deploy/systemd/cell@.service /etc/systemd/system/
sudo systemctl daemon-reload

# NUC1
sudo systemctl enable --now cell@nuc1-cell1 cell@nuc1-cell4
# NUC2
sudo systemctl enable --now cell@nuc2-cell2 cell@nuc2-cell3 cell@nuc2-cell5

systemctl status 'cell@*'
journalctl -u cell@nuc1-cell4 -f
```

Cell D (`cell5`) owns four devices: the pump, one MKS motor as a
standalone Z axis, an IKA hotplate, and an IR lamp on a Tapo plug. Two
extra prerequisites for that cell:

- The plug's credentials go in `external/SmartPlugController/secure.env`
  and its name in that driver's `device_list.md`. Without them the cell
  still starts — only the lamp endpoints answer 409.
- Never run the hotplate driver's own dashboard
  (`hotplate_controller/server.py`) at the same time as cell5: one owner
  per serial port.

### Identifying devices

Address devices by **stable VID:PID**, never by `/dev/ttyUSB*` numbering
(CLAUDE.md). When two identical adapters sit on one NUC (e.g. two CH340
pumps on NUC2), add a udev rule pinning each by its serial:

```
# /etc/udev/rules.d/70-innocoresdl.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{serial}=="<serial>", SYMLINK+="ttyPUMP_CELL2"
```

Then put `/dev/ttyPUMP_CELL2` in that cell's config.

## 3. Orchestrator (NUC1 only)

```bash
cp orchestrator/config.toml.example orchestrator/config.toml
$EDITOR orchestrator/config.toml      # real NUC IPs and ports

docker compose -f deploy/docker-compose.orch.yml up -d --build
curl -s http://127.0.0.1:17100/v1/health
curl -s http://127.0.0.1:17100/v1/cells | python -m json.tool
```

`GET /v1/cells` is the deployment check: every cell must report
`reachable: true`. An unreachable cell means the unit is down, the IP is
wrong, or a firewall blocks the port.

## 4. First real run (spec section 8.3)

Never skip a stage:

```bash
# 1. dry run — no device is touched
python -m orchestrator validate scenarios/demo_linear_move.yaml
python -m orchestrator validate scenarios/demo_cell_d_warmup.yaml

# 2. real, one step at a time, operator at the bench
python -m orchestrator run scenarios/demo_linear_move.yaml --step-mode

# 3. real, automatic
python -m orchestrator run scenarios/demo_linear_move.yaml
```

Set `target_mm` to a range the operator has confirmed is safe, and keep
the physical e-stop in reach. `POST /v1/runs/{id}/abort` broadcasts
`POST /v1/stop` to every cell — that is the *software* e-stop and does not
replace the physical button.

## 5. Reboot check

After a power cycle, `systemctl status 'cell@*'` must show every enabled
cell back up, and the compose service must have restarted the
orchestrator. That is the M6 acceptance criterion.
